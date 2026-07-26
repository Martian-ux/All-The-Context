"""B-102 configured-client witness semantics."""

from __future__ import annotations

from pathlib import Path

from allthecontext.models import CandidateInput, ClientCreate, ObservationDisposition
from allthecontext.security import (
    WITNESS_EXPLICIT_USER_STATEMENT,
    ClientPrincipal,
    principal_may_attest_explicit_user_statement,
)
from allthecontext.storage import CoreStore


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.db")
    store.initialize_vault()
    return store


def test_authentication_alone_cannot_attest_explicit_user_statement(tmp_path: Path) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(
            name="Unauthed inference client",
            scopes=["context:propose", "context:read"],
        )
    )
    assert not principal_may_attest_explicit_user_statement(principal)

    observation = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer fiction-mode short answers.",
            explicit_user_statement=True,
        ),
        client=principal,
    )

    assert observation.disposition == ObservationDisposition.TENTATIVE
    assert observation.record_id is None
    assert "witness" in (observation.decision_reason or "").casefold()


def test_configured_witness_principal_can_attest_direct_user_statement(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(
            name="Codex",
            scopes=[
                "context:propose",
                "context:read",
                WITNESS_EXPLICIT_USER_STATEMENT,
            ],
        )
    )
    assert principal.may_attest_explicit_user_statement()

    observation = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer fiction-mode detailed answers.",
            explicit_user_statement=True,
        ),
        client=principal,
    )

    assert observation.disposition == ObservationDisposition.APPLIED
    assert observation.record_id is not None
    assert store.get_record(observation.record_id).content == observation.content


def test_clients_cannot_self_escalate_to_witness_via_payload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(name="Plain propose client", scopes=["context:propose"])
    )
    # Payload claim only — principal has no grant and no admin.
    observation = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction escalation resistance.",
            explicit_user_statement=True,
            confidence=1.0,
        ),
        client=principal,
    )
    assert observation.disposition == ObservationDisposition.TENTATIVE
    # Creating another client does not alter the first principal's grants.
    store.create_client(ClientCreate(name="other", scopes=["context:read"]))
    first = next(item for item in store.list_clients() if item["name"] == "Plain propose client")
    assert WITNESS_EXPLICIT_USER_STATEMENT not in first["scopes"]
    authenticated = store.authenticate(_token)
    assert authenticated is not None
    assert not authenticated.may_attest_explicit_user_statement()


def test_unattested_inference_remains_tentative_even_with_witness_grant(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(
            name="Witness-capable client",
            scopes=["context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        )
    )
    observation = store.add_candidate(
        CandidateInput(
            kind="workflow",
            content="Uses fiction automation inferred from logs.",
            explicit_user_statement=False,
            confidence=0.95,
        ),
        client=principal,
    )
    assert observation.disposition == ObservationDisposition.TENTATIVE


def test_codex_style_witness_direct_statement_applies_while_omission_stays_tentative(
    tmp_path: Path,
) -> None:
    """Configured Codex/Claude-style witness: explicit true applies; default false does not."""

    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(
            name="Codex",
            scopes=[
                "context:ingest",
                "context:propose",
                "context:read",
                "context:status",
                WITNESS_EXPLICIT_USER_STATEMENT,
            ],
        )
    )
    direct = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer fiction-mode concise answers in this vault.",
            explicit_user_statement=True,
        ),
        client=principal,
    )
    omitted = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Maybe prefers fiction-mode verbose answers (inference only).",
            # Safer MCP default: omit explicit claim → treated as non-witness statement.
            explicit_user_statement=False,
            idempotency_key="inference-omission",
        ),
        client=principal,
    )
    assert direct.disposition == ObservationDisposition.APPLIED
    assert direct.record_id is not None
    assert store.get_record(direct.record_id).content == direct.content
    assert omitted.disposition == ObservationDisposition.TENTATIVE
    assert omitted.record_id is None


def test_admin_principal_may_attest_without_separate_witness_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(name="Desktop admin", scopes=["admin", "context:propose"])
    )
    observation = store.add_candidate(
        CandidateInput(
            kind="constraint",
            content="We must keep fiction vault disposable.",
            explicit_user_statement=True,
        ),
        client=principal,
    )
    assert observation.disposition == ObservationDisposition.APPLIED


def test_principal_helper_rejects_none_and_empty_scopes() -> None:
    assert not principal_may_attest_explicit_user_statement(None)
    assert not ClientPrincipal(
        "x", "y", frozenset({"context:propose"})
    ).may_attest_explicit_user_statement()
