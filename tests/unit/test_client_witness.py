"""B-102 configured-client witness semantics (BETA-P03 / BETA-S02 source evidence)."""

from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.memory_policy import (
    ObservationOrigin,
    effective_explicit_user_statement,
)
from allthecontext.models import (
    CandidateInput,
    ClientCreate,
    CoverageReport,
    IngestionMode,
    ObservationDisposition,
)
from allthecontext.security import (
    WITNESS_EXPLICIT_USER_STATEMENT,
    ClientPrincipal,
    principal_may_attest_explicit_user_statement,
)
from allthecontext.storage import CoreStore
from pydantic import ValidationError


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


def test_policy_helper_omission_stays_false_and_relay_never_attests() -> None:
    principal = ClientPrincipal(
        "p",
        "Codex",
        frozenset({"context:propose", WITNESS_EXPLICIT_USER_STATEMENT}),
    )
    assert effective_explicit_user_statement(
        False,
        origin=ObservationOrigin.ONGOING_CLIENT,
        principal=principal,
    ) == (False, None)
    assert effective_explicit_user_statement(
        True,
        origin=ObservationOrigin.RELAY_QUEUE,
        principal=principal,
    ) == (False, "remote relay proposals cannot attest direct user statements")


def test_archive_batch_cannot_smuggle_provider_archive_explicit_without_witness(
    tmp_path: Path,
) -> None:
    """Authenticated ingest alone must not re-label archive batches as witnessed."""

    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(
            name="Ingest-only smuggler",
            scopes=["context:ingest", "context:propose"],
        )
    )
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["fiction-export"],
        unavailable_sources=[],
        client_id=principal.id,
        idempotency_key="smuggle-begin",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "smuggle-batch",
        [
            CandidateInput(
                kind="goal",
                content="My goal is fiction smuggled provider archive explicitness.",
                observed_at="2025-01-01T00:00:00+00:00",
                explicit_user_statement=True,
                source_type="provider_archive",
                source_service="fiction-provider",
            )
        ],
        client=principal,
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fiction-export"], complete=True),
        client=principal,
    )
    observation = store.get_candidate(str(batch["candidate_ids"][0]))
    assert observation.disposition == ObservationDisposition.TENTATIVE
    assert observation.record_id is None
    assert "witness" in (observation.decision_reason or "").casefold()
    assert observation.policy_version == "automatic-v1"
    assert observation.observation_origin == ObservationOrigin.ARCHIVE_IMPORT.value


def test_core_importer_archive_path_without_client_still_applies_provider_archive(
    tmp_path: Path,
) -> None:
    """Core-controlled importer (no principal) may assign archive explicitness."""

    store = _store(tmp_path)
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["fiction-export"],
        unavailable_sources=[],
        idempotency_key="core-import-begin",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "core-import-batch",
        [
            CandidateInput(
                kind="preference",
                content="Prefer fiction short answers from provider archive.",
                observed_at="2025-02-01T00:00:00+00:00",
                explicit_user_statement=True,
                source_type="provider_archive",
                source_service="fiction-provider",
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fiction-export"], complete=True),
    )
    observation = store.get_candidate(str(batch["candidate_ids"][0]))
    assert observation.disposition == ObservationDisposition.APPLIED
    assert observation.record_id is not None
    assert observation.observation_origin == ObservationOrigin.ARCHIVE_IMPORT.value


def test_exact_witness_retry_is_idempotent_and_duplicate_reinforces(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(
            name="Codex",
            scopes=["context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        )
    )
    payload = CandidateInput(
        kind="interaction_preference",
        content="Prefer fiction-mode medium answers.",
        explicit_user_statement=True,
        idempotency_key="witness-exact-retry",
    )
    first = store.add_candidate(payload, client=principal)
    retry = store.add_candidate(payload, client=principal)
    assert first.disposition == ObservationDisposition.APPLIED
    assert retry.id == first.id
    assert retry.record_id == first.record_id
    assert retry.disposition == ObservationDisposition.APPLIED

    reinforce = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer fiction-mode medium answers.",
            explicit_user_statement=True,
            idempotency_key="witness-reinforce-duplicate",
        ),
        client=principal,
    )
    assert reinforce.disposition == ObservationDisposition.REINFORCED
    assert reinforce.record_id == first.record_id


def test_payload_cannot_smuggle_origin_disposition_or_force_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(name="Propose only", scopes=["context:propose"])
    )
    # StrictModel forbids unknown force/origin/disposition fields.
    with pytest.raises(ValidationError):
        CandidateInput.model_validate(
            {
                "kind": "goal",
                "content": "My goal is fiction force smuggle.",
                "explicit_user_statement": True,
                "observation_origin": "local_admin",
                "disposition": "applied",
                "force": True,
            }
        )
    observation = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction force smuggle without extra fields.",
            explicit_user_statement=True,
            source_type="provider_archive",
            source_service="fiction-provider",
            confidence=1.0,
        ),
        client=principal,
    )
    assert observation.disposition == ObservationDisposition.TENTATIVE
    assert observation.observation_origin == ObservationOrigin.ONGOING_CLIENT.value
    assert observation.record_id is None


def test_revoked_witness_principal_cannot_authenticate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    principal, token = store.create_client(
        ClientCreate(
            name="Codex temporary",
            scopes=["context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        )
    )
    assert store.authenticate(token) is not None
    store.revoke_client(principal.id)
    assert store.authenticate(token) is None
    listed = next(item for item in store.list_clients() if item["id"] == principal.id)
    assert listed["revoked"] is True


def test_decision_reason_and_policy_version_are_inspectable_without_secrets(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(name="Propose only", scopes=["context:propose"])
    )
    observation = store.add_candidate(
        CandidateInput(
            kind="workflow",
            content="Uses fiction shell inferred without durable credentials.",
            explicit_user_statement=True,
            evidence="no credentials here",
        ),
        client=principal,
    )
    assert observation.disposition == ObservationDisposition.TENTATIVE
    assert observation.policy_version == "automatic-v1"
    assert observation.decision_reason
    assert observation.decided_at
    reason = (observation.decision_reason or "").casefold()
    assert "witness" in reason
    # Content-free decision surface: reason/version/time, not credential material.
    assert "pbkdf2" not in reason
    assert "token_hash" not in reason


def test_star_scope_may_attest_as_intentional_local_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(name="Wildcard admin", scopes=["*", "context:propose"])
    )
    observation = store.add_candidate(
        CandidateInput(
            kind="project",
            content="I am working on fiction project Wildcard.",
            explicit_user_statement=True,
        ),
        client=principal,
    )
    assert observation.disposition == ObservationDisposition.APPLIED


def test_forged_principal_scopes_cannot_manufacture_witness_authority(tmp_path: Path) -> None:
    """Caller-supplied ClientPrincipal scopes are re-bound from durable registration."""

    store = _store(tmp_path)
    plain, _token = store.create_client(
        ClientCreate(name="Plain", scopes=["context:propose", "context:ingest"])
    )
    forged = ClientPrincipal(
        plain.id,
        "forged-witness-shape",
        frozenset({"context:propose", "context:ingest", WITNESS_EXPLICIT_USER_STATEMENT}),
    )
    propose = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction forged principal scopes.",
            explicit_user_statement=True,
        ),
        client=forged,
    )
    assert propose.disposition == ObservationDisposition.TENTATIVE
    assert propose.record_id is None

    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["fiction"],
        unavailable_sources=[],
        client_id=plain.id,
        idempotency_key="forged-archive",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "forged-batch",
        [
            CandidateInput(
                kind="preference",
                content="Prefer fiction forged archive explicitness.",
                explicit_user_statement=True,
                source_type="provider_archive",
                observed_at="2025-03-01T00:00:00+00:00",
            )
        ],
        client=forged,
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fiction"], complete=True),
        client=forged,
    )
    archive = store.get_candidate(str(batch["candidate_ids"][0]))
    assert archive.disposition == ObservationDisposition.TENTATIVE
    assert archive.record_id is None
    assert "witness" in (archive.decision_reason or "").casefold()


def test_evaluate_staged_rebinds_non_witness_and_preserves_core_importer(
    tmp_path: Path,
) -> None:
    """Crash-recovery re-eval must not fail open for authenticated archive batches."""

    import sqlite3

    store = _store(tmp_path)
    plain, _token = store.create_client(
        ClientCreate(name="Plain", scopes=["context:ingest", "context:propose"])
    )

    client_session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["client-export"],
        unavailable_sources=[],
        client_id=plain.id,
        idempotency_key="staged-client",
    )
    client_batch = store.submit_batch(
        str(client_session["session_id"]),
        "staged-client-batch",
        [
            CandidateInput(
                kind="goal",
                content="My goal is fiction staged client smuggle.",
                explicit_user_statement=True,
                source_type="provider_archive",
                observed_at="2025-01-01T00:00:00+00:00",
            )
        ],
        client=plain,
    )
    core_session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["core-export"],
        unavailable_sources=[],
        idempotency_key="staged-core",
    )
    core_batch = store.submit_batch(
        str(core_session["session_id"]),
        "staged-core-batch",
        [
            CandidateInput(
                kind="goal",
                content="My goal is fiction staged core importer apply.",
                explicit_user_statement=True,
                source_type="provider_archive",
                observed_at="2025-02-01T00:00:00+00:00",
            )
        ],
    )
    with sqlite3.connect(store.database_path) as connection:
        for session_id in (client_session["session_id"], core_session["session_id"]):
            connection.execute(
                "UPDATE ingestion_sessions SET status='finished', coverage_json='{}', "
                "finished_at='2025-01-01T00:00:00+00:00' WHERE id=?",
                (session_id,),
            )
        connection.commit()

    evaluated = store.evaluate_staged_observations()
    assert evaluated >= 2
    client_obs = store.get_candidate(str(client_batch["candidate_ids"][0]))
    core_obs = store.get_candidate(str(core_batch["candidate_ids"][0]))
    assert client_obs.disposition == ObservationDisposition.TENTATIVE
    assert client_obs.record_id is None
    assert core_obs.disposition == ObservationDisposition.APPLIED
    assert core_obs.record_id is not None


def test_relay_and_provider_memory_cannot_become_witnessed_user_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    relay, _replayed = store.add_edge_candidate(
        "relay-explicit-1",
        CandidateInput(
            kind="goal",
            content="My goal is fiction relay provider-archive explicit.",
            explicit_user_statement=True,
            source_type="provider_archive",
        ),
        client_id="edge:fiction-client",
    )
    assert relay.disposition == ObservationDisposition.TENTATIVE
    assert relay.decision_reason == "explicit_user_statement_witness_required"
    assert relay.observation_origin == ObservationOrigin.RELAY_QUEUE.value
    assert relay.record_id is None

    restarted = CoreStore(store.database_path)
    assert restarted.migrate() == 20
    repaired_relay = restarted.get_candidate(relay.id)
    assert repaired_relay.decision_reason == "explicit_user_statement_witness_required"
    assert repaired_relay.observation_origin == ObservationOrigin.RELAY_QUEUE.value
    assert repaired_relay.disposition == ObservationDisposition.TENTATIVE
    assert repaired_relay.record_id is None

    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["memory-export"],
        unavailable_sources=[],
        idempotency_key="provider-memory",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "memory-batch",
        [
            CandidateInput(
                kind="provider_memory",
                content="Provider synthesis about fiction preferences.",
                explicit_user_statement=False,
                source_type="provider_memory",
                source_service="chatgpt",
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["memory-export"], complete=True),
    )
    memory = store.get_candidate(str(batch["candidate_ids"][0]))
    assert memory.disposition == ObservationDisposition.TENTATIVE
    assert memory.record_id is None


def test_authorized_desktop_ai_scopes_still_apply_via_archive_and_propose(
    tmp_path: Path,
) -> None:
    from allthecontext.desktop_setup import AI_CLIENT_SCOPES

    store = _store(tmp_path)
    principal, _token = store.create_client(
        ClientCreate(name="Codex", scopes=list(AI_CLIENT_SCOPES))
    )
    assert WITNESS_EXPLICIT_USER_STATEMENT in principal.scopes
    proposed = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer fiction-mode desktop AI witness answers.",
            explicit_user_statement=True,
        ),
        client=principal,
    )
    assert proposed.disposition == ObservationDisposition.APPLIED
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["desktop-export"],
        unavailable_sources=[],
        client_id=principal.id,
        idempotency_key="desktop-ai-archive",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "desktop-batch",
        [
            CandidateInput(
                kind="constraint",
                content="We must keep fiction desktop AI archive attested.",
                explicit_user_statement=True,
                source_type="provider_archive",
                observed_at="2025-04-01T00:00:00+00:00",
            )
        ],
        client=principal,
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["desktop-export"], complete=True),
        client=principal,
    )
    archive = store.get_candidate(str(batch["candidate_ids"][0]))
    assert archive.disposition == ObservationDisposition.APPLIED
    assert archive.record_id is not None
