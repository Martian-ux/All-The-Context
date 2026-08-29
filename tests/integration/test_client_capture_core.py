from __future__ import annotations

import json
import uuid
from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.export import _decrypt_file, create_export
from allthecontext.lifecycle_contract import MAX_LIFECYCLE_BODY_BYTES, MAX_LIFECYCLE_CONTENT_CHARS
from allthecontext.lifecycle_runtime import LifecycleRuntimeAdapter
from allthecontext.models import ClientCreate
from fastapi.testclient import TestClient


def _event(*, event_id: str = "evt-1", role: str = "user", content: str = "I prefer tabs"):
    return {
        "event_id": event_id,
        "idempotency_key": str(uuid.uuid4()),
        "session_id": "session-1",
        "conversation_id": "conversation-1",
        "sequence": 1,
        "role": role,
        "content": content,
    }


def _headers(token: str, client_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-ATC-Client-ID": client_id}


def test_lifecycle_content_and_http_body_bounds_are_core_consistent(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="Bounded capture", scopes=["context:capture"])
        )
        with TestClient(create_app(config, service=service)) as client:
            headers = _headers(token, principal.id)
            oversized_content = client.post(
                "/v1/lifecycle/events",
                headers=headers,
                json=_event(content="x" * (MAX_LIFECYCLE_CONTENT_CHARS + 1)),
            )
            assert oversized_content.status_code == 422

            oversized_body = client.post(
                "/v1/lifecycle/events",
                headers=headers,
                json={"extra": "x" * MAX_LIFECYCLE_BODY_BYTES},
            )
            assert oversized_body.status_code == 413
            assert oversized_body.json()["error"]["code"] == "request_too_large"


def test_capture_scope_is_provisioned_only_when_explicitly_requested(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        setup = client.post("/v1/setup", json={"name": "Owner", "scopes": []})
        assert setup.status_code == 200, setup.text
        owner_headers = {"Authorization": f"Bearer {setup.json()['token']}"}

        ordinary = client.post(
            "/v1/admin/clients",
            headers=owner_headers,
            json={"name": "Ordinary client"},
        )
        assert ordinary.status_code == 200, ordinary.text
        assert "context:capture" not in ordinary.json()["client"]["scopes"]

        opted_in = client.post(
            "/v1/admin/clients",
            headers=owner_headers,
            json={"name": "Opted-in capture client", "scopes": ["context:capture"]},
        )
        assert opted_in.status_code == 200, opted_in.text
        assert opted_in.json()["client"]["scopes"] == ["context:capture"]


def test_lifecycle_capture_forms_user_memory_and_is_replayable(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service:
        capture_principal, token = service.store.create_client(
            ClientCreate(name="Codex capture", scopes=["context:capture", "context:read"])
        )
        propose_only, propose_token = service.store.create_client(
            ClientCreate(name="Proposal only", scopes=["context:propose"])
        )
        with TestClient(create_app(config, service=service)) as client:
            headers = _headers(token, capture_principal.id)
            first_payload = _event()
            first = client.post("/v1/lifecycle/events", headers=headers, json=first_payload)
            assert first.status_code == 200, first.text
            assert first.json()["ok"] is True
            assert first.json()["status"] == "captured"
            assert set(first.json()) == {"ok", "status", "capture_event_id", "observation_id"}

            replay = client.post("/v1/lifecycle/events", headers=headers, json=first_payload)
            assert replay.status_code == 200, replay.text
            assert replay.json()["status"] == "replayed"
            assert replay.json()["capture_event_id"] == first.json()["capture_event_id"]
            assert replay.json()["observation_id"] == first.json()["observation_id"]

            invalid_authority = client.post(
                "/v1/lifecycle/events",
                headers=headers,
                json={**_event(event_id="evt-2"), "sensitivity": "highly_sensitive"},
            )
            assert invalid_authority.status_code == 422

            invalid_idempotency = client.post(
                "/v1/lifecycle/events",
                headers=headers,
                json={**_event(event_id="evt-3"), "idempotency_key": "not-a-uuid"},
            )
            assert invalid_idempotency.status_code == 422

            denied = client.post(
                "/v1/lifecycle/events",
                headers=_headers(propose_token, propose_only.id),
                json=_event(event_id="evt-4"),
            )
            assert denied.status_code == 403

        with service.store.connect() as connection:
            event = connection.execute(
                "SELECT * FROM capture_events WHERE provider_event_id=?", ("evt-1",)
            ).fetchone()
            assert event is not None
            assert event["status"] == "applied"
            assert event["idempotency_key"] == first_payload["idempotency_key"]
            assert json.loads(event["normalized_payload_json"])["role"] == "user"
            formed = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (first.json()["observation_id"],)
            ).fetchone()
            assert formed is not None
            assert formed["source_type"] == "client_capture_formation"
            assert formed["kind"] == "interaction_preference"
            assert formed["explicit_user_statement"] == 0
            assert formed["disposition"] == "applied"
            assert json.loads(formed["structured_value_json"])["capture_role"] == "user"
            assert formed["capture_event_id"] == event["id"]
            raw = connection.execute(
                "SELECT * FROM context_candidates WHERE capture_event_id=? "
                "AND source_type='client_capture'",
                (event["id"],),
            ).fetchone()
            assert raw is not None
            assert raw["disposition"] == "tentative"
            assert connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 1


def test_capture_preserves_roles_and_allows_sensitive_personal_context(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="Claude Code capture", scopes=["context:capture", "context:read"])
        )
        with TestClient(create_app(config, service=service)) as client:
            headers = _headers(token, principal.id)
            for sequence, role, content in (
                (1, "assistant", "The assistant response is observable evidence."),
                (2, "tool", "The tool returned a local result."),
                (3, "imported", "Imported text is untrusted evidence."),
                (4, "user", "I live in 12 Main Street."),
                (5, "user", "My SSN is 123-45-6789."),
            ):
                response = client.post(
                    "/v1/lifecycle/events",
                    headers=headers,
                    json={
                        **_event(event_id=f"evt-{sequence}", role=role, content=content),
                        "sequence": sequence,
                    },
                )
                assert response.status_code == 200, response.text

        with service.store.connect() as connection:
            rows = connection.execute(
                "SELECT sensitivity,availability,allowed_clients_json,explicit_user_statement,"
                "disposition,structured_value_json,source_type FROM context_candidates "
                "ORDER BY created_at,id"
            ).fetchall()
            assert len(rows) == 7
            raw_rows = [row for row in rows if row["source_type"] == "client_capture"]
            formed_rows = [row for row in rows if row["source_type"] == "client_capture_formation"]
            assert len(raw_rows) == 5
            assert len(formed_rows) == 2
            assert all(row["explicit_user_statement"] == 0 for row in raw_rows)
            assert all(row["disposition"] == "tentative" for row in raw_rows)
            assert all(row["disposition"] == "applied" for row in formed_rows)
            address = next(row for row in formed_rows if row["sensitivity"] == "sensitive")
            assert address["availability"] == "local_only"
            assert json.loads(address["allowed_clients_json"]) == []
            ssn = next(row for row in formed_rows if row["sensitivity"] == "highly_sensitive")
            assert ssn["availability"] == "local_only"
            assert json.loads(ssn["allowed_clients_json"]) == []

            raw_private_rows = [
                row for row in raw_rows if row["sensitivity"] in {"sensitive", "highly_sensitive"}
            ]
            assert len(raw_private_rows) == 2
            assert all(
                json.loads(row["allowed_clients_json"]) == [principal.id]
                for row in raw_private_rows
            )


def test_live_user_reconciliation_deduplicates_and_replaces_preferences(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="User capture", scopes=["context:capture", "context:read"])
        )
        with TestClient(create_app(config, service=service)) as client:
            headers = _headers(token, principal.id)
            for sequence, role, content in (
                (1, "user", "I prefer tabs"),
                (2, "assistant", "The user prefers tabs"),
                (3, "user", "I now prefer spaces"),
                (4, "user", "I now prefer spaces"),
            ):
                response = client.post(
                    "/v1/lifecycle/events",
                    headers=headers,
                    json={
                        **_event(event_id=f"preference-{sequence}", role=role, content=content),
                        "sequence": sequence,
                    },
                )
                assert response.status_code == 200, response.text

            search = client.post(
                "/v1/context/search",
                headers=headers,
                json={"query": "prefer", "kinds": ["interaction_preference"]},
            )
            assert search.status_code == 200, search.text
            assert [item["content"] for item in search.json()["items"]] == ["I now prefer spaces"]

        with service.store.connect() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM context_records WHERE kind='interaction_preference'"
                ).fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM context_candidates "
                    "WHERE source_type='client_capture_formation'"
                ).fetchone()[0]
                == 3
            )
            assistant = connection.execute(
                "SELECT COUNT(*) FROM context_candidates "
                "WHERE source_type='client_capture_formation' AND content=?",
                ("The user prefers tabs",),
            ).fetchone()[0]
            assert assistant == 0


def test_sensitive_personal_memory_reaches_local_reader_and_secrets_stay_out_of_export(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    token_canary = "ATC_API_TOKEN_CANARY_9X7Q"
    key_canary = "ATC_PRIVATE_KEY_CANARY_2K8M"
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="Protected capture", scopes=["context:capture"])
        )
        other, other_token = service.store.create_client(
            ClientCreate(name="Other reader", scopes=["context:read"])
        )
        with TestClient(create_app(config, service=service)) as client:
            headers = _headers(token, principal.id)
            for sequence, content in (
                (1, "My SSN is 123-45-6789."),
                (2, "I have diabetes."),
            ):
                response = client.post(
                    "/v1/lifecycle/events",
                    headers=headers,
                    json={
                        **_event(event_id=f"private-{sequence}", content=content),
                        "sequence": sequence,
                    },
                )
                assert response.status_code == 200, response.text
            for sequence, content in (
                (3, f"api key: {token_canary}"),
                (4, f"-----BEGIN PRIVATE KEY----- {key_canary}"),
            ):
                response = client.post(
                    "/v1/lifecycle/events",
                    headers=headers,
                    json={
                        **_event(event_id=f"secret-{sequence}", content=content),
                        "sequence": sequence,
                    },
                )
                assert response.status_code == 200
                assert response.json()["status"] == "refused"
                assert token_canary not in response.text
                assert key_canary not in response.text

            capture_cannot_read = client.post(
                "/v1/context/search",
                headers=headers,
                json={"query": "diabetes", "limit": 10},
            )
            other_reader = client.post(
                "/v1/context/search",
                headers=_headers(other_token, other.id),
                json={"query": "diabetes", "limit": 10},
            )
            assert capture_cannot_read.status_code == 403
            assert other_reader.status_code == 200
            assert [item["content"] for item in other_reader.json()["items"]] == [
                "I have diabetes."
            ]

        with service.store.connect() as connection:
            serialized = " ".join(
                str(row[0])
                for table in ("capture_events", "context_candidates", "context_records")
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            )
            assert token_canary not in serialized
            assert key_canary not in serialized
        export_path = tmp_path / "capture-export.atcexp"
        create_export(config.database_path, export_path, "capture-test-passphrase")
        decrypted = tmp_path / "capture-export.zip"
        _decrypt_file(export_path, decrypted, "capture-test-passphrase")
        assert token_canary.encode() not in decrypted.read_bytes()
        assert key_canary.encode() not in decrypted.read_bytes()


def test_operational_secret_is_refused_before_capture_or_observation_persistence(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    canary = "ATC_CAPTURE_SECRET_CANARY_7Q2Z9M"
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="Capture secret test", scopes=["context:capture"])
        )
        payload = _event(content=f"password: {canary}")
        with TestClient(create_app(config, service=service)) as client:
            discussion = client.post(
                "/v1/lifecycle/events",
                headers=_headers(token, principal.id),
                json={
                    **_event(
                        event_id="discussion-1",
                        content="My API key rotates quarterly",
                    ),
                },
            )
            assert discussion.status_code == 200, discussion.text
            assert discussion.json()["status"] == "captured"
            response = client.post(
                "/v1/lifecycle/events",
                headers=_headers(token, principal.id),
                json=payload,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["ok"] is False
            assert body["status"] == "refused"
            assert body["reason"] == "direct_secret_like_content"
            assert canary not in response.text
        with service.store.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM context_candidates").fetchone()[0] == 1
            receipts = connection.execute(
                "SELECT route,reason_code FROM secret_refusal_receipts"
            ).fetchall()
            assert [(row["route"], row["reason_code"]) for row in receipts] == [
                ("/v1/lifecycle/events", "direct_secret_like_content")
            ]


def test_lifecycle_adapter_reaches_core_formation_and_retrieval(tmp_path: Path) -> None:
    """Exercise the integrated flat adapter contract through the real Core route."""

    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service:
        capture_principal, capture_token = service.store.create_client(
            ClientCreate(name="Codex capture", scopes=["context:capture"])
        )
        reader, reader_token = service.store.create_client(
            ClientCreate(name="Codex reader", scopes=["context:read"])
        )
        with TestClient(create_app(config, service=service)) as client:

            class RouteClient:
                def capture_lifecycle_event(self, payload: dict[str, object]) -> object:
                    response = client.post(
                        "/v1/lifecycle/events",
                        headers=_headers(capture_token, capture_principal.id),
                        json=payload,
                    )
                    response.raise_for_status()
                    return response.json()

            runtime = LifecycleRuntimeAdapter(
                provider="codex",
                client_id=capture_principal.id,
                core=RouteClient(),
            )
            observed = runtime.observe_user_turn(
                prompt="I prefer concise answers",
                session_id="session-integrated",
                turn_id="turn-1",
                retrieve=False,
            )
            completed = runtime.observe_assistant_response(
                response="I will keep the answer concise.",
                session_id="session-integrated",
                turn_id="turn-1",
            )

            assert observed.capture.status == "captured"
            assert completed.capture.status == "captured"
            assert completed.pairing == "paired"
            search = client.post(
                "/v1/context/search",
                headers=_headers(reader_token, reader.id),
                json={"query": "concise", "kinds": ["interaction_preference"]},
            )
            assert search.status_code == 200, search.text
            assert [item["content"] for item in search.json()["items"]] == [
                "I prefer concise answers"
            ]
