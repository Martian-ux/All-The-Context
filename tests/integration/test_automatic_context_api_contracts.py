from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.models import CandidateInput
from fastapi.testclient import TestClient


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_client(
    client: TestClient,
    owner_headers: dict[str, str],
    *,
    name: str,
    scopes: list[str],
) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/v1/admin/clients",
        headers=owner_headers,
        json={"name": name, "scopes": scopes},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return str(payload["client"]["id"]), _bearer(str(payload["token"]))


def _setup_two_clients(
    client: TestClient,
) -> tuple[dict[str, str], tuple[str, dict[str, str]], tuple[str, dict[str, str]]]:
    setup = client.post("/v1/setup", json={"name": "Owner", "scopes": []})
    assert setup.status_code == 200, setup.text
    owner_headers = _bearer(str(setup.json()["token"]))
    scopes = ["context:ingest", "context:propose", "context:read", "context:status"]
    first = _create_client(
        client,
        owner_headers,
        name="First app",
        scopes=scopes,
    )
    second = _create_client(
        client,
        owner_headers,
        name="Second app",
        scopes=scopes,
    )
    return owner_headers, first, second


def test_authenticated_clients_cannot_submit_or_finish_each_others_session(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        _owner, (_first_id, first_headers), (_second_id, second_headers) = (
            _setup_two_clients(client)
        )
        begun = client.post(
            "/v1/ingestion/begin",
            headers=first_headers,
            json={
                "mode": "model_assisted_bootstrap",
                "accessible_sources": ["current conversation"],
                "unavailable_sources": [],
                "idempotency_key": "first-session",
            },
        )
        assert begun.status_code == 200, begun.text
        session_id = str(begun.json()["session_id"])
        batch_payload: dict[str, Any] = {
            "session_id": session_id,
            "idempotency_key": "batch-1",
            "candidates": [
                {
                    "kind": "interaction_preference",
                    "content": "Keep ingestion sessions bound to their authenticated client.",
                    "explicit_user_statement": True,
                }
            ],
        }
        finish_payload = {
            "session_id": session_id,
            "coverage_report": {
                "available": ["current conversation"],
                "unavailable": [],
                "complete": True,
            },
        }

        denied_batch = client.post(
            "/v1/ingestion/batch",
            headers=second_headers,
            json=batch_payload,
        )
        denied_finish = client.post(
            "/v1/ingestion/finish",
            headers=second_headers,
            json=finish_payload,
        )

        assert denied_batch.status_code == 404
        assert denied_finish.status_code == 404
        accepted_batch = client.post(
            "/v1/ingestion/batch",
            headers=first_headers,
            json=batch_payload,
        )
        assert accepted_batch.status_code == 200, accepted_batch.text
        accepted_finish = client.post(
            "/v1/ingestion/finish",
            headers=first_headers,
            json=finish_payload,
        )
        assert accepted_finish.status_code == 200, accepted_finish.text
        assert accepted_finish.json()["status"] == "finished"


def test_denied_client_cannot_mutate_target_through_any_observation_endpoint(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        owner_headers, (allowed_id, allowed_headers), (_denied_id, denied_headers) = (
            _setup_two_clients(client)
        )
        created = client.post(
            "/v1/ingestion/propose",
            headers=owner_headers,
            json={
                "kind": "interaction_preference",
                "content": "Prefer concise answers.",
                "allowed_clients": [allowed_id],
                "explicit_user_statement": True,
            },
        )
        assert created.status_code == 200, created.text
        record_id = str(created.json()["record_id"])

        reported = client.post(
            "/v1/ingestion/error",
            headers=denied_headers,
            json={
                "record_id": record_id,
                "description": "Attempt to replace inaccessible context.",
                "suggested_correction": "Prefer very long answers.",
            },
        )
        forgotten = client.post(
            "/v1/ingestion/forget",
            headers=denied_headers,
            json={
                "record_id": record_id,
                "reason": "Attempt to forget inaccessible context.",
            },
        )
        superseded = client.post(
            "/v1/ingestion/propose",
            headers=denied_headers,
            json={
                "kind": "correction",
                "content": "Prefer very long answers.",
                "supersedes": record_id,
                "explicit_user_statement": True,
            },
        )

        assert reported.status_code == 404
        assert forgotten.status_code == 404
        assert superseded.status_code == 200, superseded.text
        assert superseded.json()["disposition"] == "ignored"
        current = client.get(f"/v1/context/{record_id}", headers=allowed_headers)
        assert current.status_code == 200, current.text
        assert current.json()["content"] == "Prefer concise answers."


def test_context_error_accepts_new_and_legacy_shapes_and_rejects_blank_text(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/v1/ingestion/propose",
            json={
                "kind": "fact",
                "content": "The user lives in Portland.",
                "explicit_user_statement": True,
            },
        )
        assert created.status_code == 200, created.text
        record_id = str(created.json()["record_id"])

        new_shape = client.post(
            "/v1/ingestion/error",
            json={
                "record_id": record_id,
                "description": "The stored city is out of date.",
                "suggested_correction": "The user lives in Boston.",
            },
        )
        assert new_shape.status_code == 200, new_shape.text
        assert new_shape.json()["disposition"] == "applied"
        assert client.get(f"/v1/context/{record_id}").json()["content"] == (
            "The user lives in Boston."
        )

        legacy_shape = client.post(
            "/v1/ingestion/error",
            json={
                "record_id": record_id,
                "description": "The legacy client also reports a newer city.",
                "content": "The user lives in Chicago.",
            },
        )
        assert legacy_shape.status_code == 200, legacy_shape.text
        assert legacy_shape.json()["disposition"] == "applied"
        assert client.get(f"/v1/context/{record_id}").json()["content"] == (
            "The user lives in Chicago."
        )

        blank_payloads = [
            {
                "record_id": record_id,
                "description": "   ",
            },
            {
                "record_id": record_id,
                "description": "There is a correction.",
                "suggested_correction": "\t",
            },
            {
                "record_id": record_id,
                "description": "Legacy correction is blank.",
                "content": "\n",
            },
        ]
        for payload in blank_payloads:
            response = client.post("/v1/ingestion/error", json=payload)
            assert response.status_code == 422, response.text

        assert client.get(f"/v1/context/{record_id}").json()["content"] == (
            "The user lives in Chicago."
        )


def test_context_error_retry_reuses_observation_and_provenance_row(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/v1/ingestion/propose",
            json={
                "kind": "fact",
                "content": "The user lives in Portland.",
                "explicit_user_statement": True,
            },
        )
        record_id = str(created.json()["record_id"])
        payload = {
            "record_id": record_id,
            "description": "The stored city is out of date.",
            "suggested_correction": "The user lives in Boston.",
            "idempotency_key": "same-error",
        }

        first = client.post("/v1/ingestion/error", json=payload)
        second = client.post("/v1/ingestion/error", json=payload)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]

    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM context_errors WHERE candidate_id=?",
            (first.json()["id"],),
        ).fetchone()[0] == 1


def test_relay_queued_writes_preserve_client_acl_at_core(tmp_path: Path) -> None:
    core = CoreService(CoreConfig.in_directory(tmp_path, require_auth=False))
    created = core.store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer concise answers.",
            allowed_clients=["allowed-client"],
            explicit_user_statement=True,
        )
    )
    assert created.record_id is not None

    correction, correction_replayed = core.store.add_edge_candidate(
        "denied-correction",
        CandidateInput(
            kind="correction",
            content="Prefer very long answers.",
            supersedes=created.record_id,
            explicit_user_statement=True,
        ),
        client_id="denied-client",
    )
    forgotten, forget_replayed = core.store.add_edge_candidate(
        "denied-forget",
        CandidateInput(
            kind="context_forget",
            content="The user explicitly requested deletion.",
            supersedes=created.record_id,
            explicit_user_statement=True,
        ),
        client_id="denied-client",
    )

    assert correction_replayed is False
    assert forget_replayed is False
    assert correction.disposition == "ignored"
    assert forgotten.disposition == "ignored"
    assert core.store.get_observation(correction.id).submitted_by_client_id == (
        "denied-client"
    )
    assert core.store.get_observation(forgotten.id).submitted_by_client_id == (
        "denied-client"
    )
    assert core.store.get_record(created.record_id).content == "Prefer concise answers."
