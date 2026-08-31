from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.export import _decrypt_file, create_export
from allthecontext.security import WITNESS_EXPLICIT_USER_STATEMENT
from fastapi.testclient import TestClient

PREFERENCE = "I prefer concise answers."
CORRECTED_PREFERENCE = "I prefer evidence-backed answers."
SECRET_CANARY = "ATC_COMPOSED_SECRET_CANARY_6N4P8W"


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


def _capture_event(
    client: TestClient,
    *,
    client_id: str,
    headers: dict[str, str],
    event_id: str,
    idempotency_key: str,
    sequence: int,
    role: str,
    content: str,
) -> None:
    payload: dict[str, Any] = {
        "event_id": event_id,
        "idempotency_key": idempotency_key,
        "session_id": "cross-client-session",
        "conversation_id": "cross-client-conversation",
        "sequence": sequence,
        "role": role,
        "content": content,
        "observed_at": f"2026-08-30T12:00:{sequence:02d}Z",
    }
    request_headers = {**headers, "X-ATC-Client-ID": client_id}
    first = client.post(
        "/v1/lifecycle/events",
        headers=request_headers,
        json=payload,
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "captured"

    replay = client.post(
        "/v1/lifecycle/events",
        headers=request_headers,
        json=payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "replayed"
    assert replay.json()["capture_event_id"] == first.json()["capture_event_id"]
    assert replay.json()["observation_id"] == first.json()["observation_id"]


def _fixed_v4_key(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _fetch_rows(
    database: Path,
    query: str,
    parameters: tuple[object, ...] = (),
) -> list[dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def _table_counts(database: Path) -> tuple[int, int, int]:
    rows = _fetch_rows(
        database,
        "SELECT "
        "(SELECT COUNT(*) FROM capture_events) AS capture_events, "
        "(SELECT COUNT(*) FROM context_candidates) AS observations, "
        "(SELECT COUNT(*) FROM context_records) AS records",
    )
    assert len(rows) == 1
    row = rows[0]
    return int(row["capture_events"]), int(row["observations"]), int(row["records"])


def _assert_bootstrap_contains(
    client: TestClient,
    headers: dict[str, str],
    *,
    query: str,
    expected: str | None,
) -> dict[str, Any]:
    response = client.post(
        "/v1/context/bootstrap",
        headers=headers,
        json={"task_description": query, "character_budget": 4_000},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["context_mode"] == "local_core"
    contents = [item["content"] for item in payload["items"]]
    if expected is not None:
        assert expected in contents
    return payload


def _assert_canary_absent_from_tree(root: Path, canary: str) -> None:
    needle = canary.encode("utf-8")
    for path in root.rglob("*"):
        if path.is_file():
            assert needle not in path.read_bytes(), path


def test_composed_cross_client_memory_acceptance_over_disposable_vault(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)

    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        setup = client.post("/v1/setup", json={"name": "Acceptance owner", "scopes": []})
        assert setup.status_code == 200, setup.text
        owner_headers = _bearer(str(setup.json()["token"]))

        codex_read_id, codex_read_headers = _create_client(
            client,
            owner_headers,
            name="Codex read",
            scopes=["context:read"],
        )
        codex_capture_id, codex_capture_headers = _create_client(
            client,
            owner_headers,
            name="Codex capture",
            scopes=["context:capture"],
        )
        codex_write_id, codex_write_headers = _create_client(
            client,
            owner_headers,
            name="Codex explicit-write",
            scopes=["context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        )
        hermes_read_id, hermes_read_headers = _create_client(
            client,
            owner_headers,
            name="Hermes read",
            scopes=["context:read"],
        )
        hermes_capture_id, hermes_capture_headers = _create_client(
            client,
            owner_headers,
            name="Hermes capture",
            scopes=["context:capture"],
        )

        listed = client.get("/v1/admin/clients", headers=owner_headers)
        assert listed.status_code == 200, listed.text
        by_name = {item["name"]: item for item in listed.json()["items"]}
        assert by_name["Codex read"]["scopes"] == ["context:read"]
        assert by_name["Codex capture"]["scopes"] == ["context:capture"]
        assert set(by_name["Codex explicit-write"]["scopes"]) == {
            "context:propose",
            WITNESS_EXPLICIT_USER_STATEMENT,
        }
        assert by_name["Hermes read"]["scopes"] == ["context:read"]
        assert by_name["Hermes capture"]["scopes"] == ["context:capture"]
        assert (
            len(
                {
                    codex_read_id,
                    codex_capture_id,
                    codex_write_id,
                    hermes_read_id,
                    hermes_capture_id,
                }
            )
            == 5
        )

        # These deterministic user/assistant/tool/imported turns provide a
        # sustained exactly-once retry stream through the same public route.
        roles_and_content = (
            ("user", PREFERENCE),
            ("assistant", "The user prefers verbose answers."),
            ("tool", "The preference lookup returned verbose answers."),
            ("imported", "Provider transcript says the user prefers verbose answers."),
        ) * 3
        for sequence, (role, content) in enumerate(roles_and_content, start=1):
            _capture_event(
                client,
                client_id=codex_capture_id,
                headers=codex_capture_headers,
                event_id=f"codex-event-{sequence}",
                idempotency_key=_fixed_v4_key(sequence),
                sequence=sequence,
                role=role,
                content=content,
            )

        # A second client observes the same user-authored claim. The
        # formed evidence reinforces one canonical slot instead of adding
        # another current record.
        _capture_event(
            client,
            client_id=hermes_capture_id,
            headers=hermes_capture_headers,
            event_id="hermes-equivalent-user",
            idempotency_key=_fixed_v4_key(20),
            sequence=1,
            role="user",
            content=PREFERENCE,
        )

        candidate_rows = _fetch_rows(
            config.database_path,
            "SELECT kind,content,structured_value_json,source_service,source_type,"
            "observation_origin,disposition,explicit_user_statement,record_id,"
            "submitted_by_client_id "
            "FROM context_candidates ORDER BY created_at,id",
        )
        raw_rows = [row for row in candidate_rows if row["source_type"] == "client_capture"]
        formed_rows = [
            row for row in candidate_rows if row["source_type"] == "client_capture_formation"
        ]
        assert len(raw_rows) == 13
        assert len(formed_rows) == 4
        assert Counter(
            json.loads(row["structured_value_json"])["capture_role"] for row in raw_rows
        ) == Counter({"user": 4, "assistant": 3, "tool": 3, "imported": 3})
        assert Counter(row["submitted_by_client_id"] for row in raw_rows) == Counter(
            {codex_capture_id: 12, hermes_capture_id: 1}
        )
        assert all(
            row["observation_origin"] == "client_capture"
            and row["disposition"] == "tentative"
            and not row["explicit_user_statement"]
            and row["record_id"] is None
            for row in raw_rows
        )
        assert all(
            row["observation_origin"] == "live_user_evidence"
            and row["disposition"] in {"applied", "reinforced"}
            and not row["explicit_user_statement"]
            and row["source_service"] == "allthecontext-core"
            and json.loads(row["structured_value_json"])["capture_role"] == "user"
            for row in formed_rows
        )

        records = _fetch_rows(
            config.database_path,
            "SELECT id,kind,content,entity_key,attribute_key,source_service,source_type,"
            "observation_origin,explicit_user_statement FROM context_records "
            "WHERE deleted_at IS NULL",
        )
        assert len(records) == 1
        record = records[0]
        record_id = str(record["id"])
        assert {
            "kind": record["kind"],
            "content": record["content"],
            "entity_key": record["entity_key"],
            "attribute_key": record["attribute_key"],
            "source_service": record["source_service"],
            "source_type": record["source_type"],
            "observation_origin": record["observation_origin"],
        } == {
            "kind": "interaction_preference",
            "content": PREFERENCE,
            "entity_key": "user",
            "attribute_key": "response_style",
            "source_service": "allthecontext-core",
            "source_type": "client_capture_formation",
            "observation_origin": "live_user_evidence",
        }
        assert not record["explicit_user_statement"]

        # Hermes receives the authorized memory through the real
        # pre-generation bootstrap surface before any generation occurs.
        _assert_bootstrap_contains(
            client,
            hermes_read_headers,
            query="How should the next answer be written?",
            expected=PREFERENCE,
        )
        codex_read_search = client.post(
            "/v1/context/search",
            headers=codex_read_headers,
            json={"query": "concise answers", "kinds": ["interaction_preference"]},
        )
        assert codex_read_search.status_code == 200, codex_read_search.text
        assert [item["content"] for item in codex_read_search.json()["items"]] == [PREFERENCE]

    # Closing the service checkpoints SQLite/WAL state. A new Core over the
    # same vault must preserve current truth and the second reader's bootstrap.
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        _assert_bootstrap_contains(
            client,
            hermes_read_headers,
            query="Resume the answer-writing task after Core restart.",
            expected=PREFERENCE,
        )
        _assert_bootstrap_contains(
            client,
            codex_read_headers,
            query="Resume the answer-writing task after Core restart.",
            expected=PREFERENCE,
        )

        # Ordinary capture refuses an operational credential before any
        # capture event, observation, or current-memory row is persisted.
        counts_before_refusal = _table_counts(config.database_path)
        refusal = client.post(
            "/v1/lifecycle/events",
            headers={**codex_capture_headers, "X-ATC-Client-ID": codex_capture_id},
            json={
                "event_id": "codex-secret-canary",
                "idempotency_key": _fixed_v4_key(21),
                "session_id": "cross-client-session",
                "conversation_id": "cross-client-conversation",
                "sequence": 21,
                "role": "user",
                "content": f"password: {SECRET_CANARY}",
            },
        )
        assert refusal.status_code == 200, refusal.text
        assert refusal.json()["status"] == "refused"
        assert refusal.json()["reason"] == "direct_secret_like_content"
        assert SECRET_CANARY not in refusal.text
        assert _table_counts(config.database_path) == counts_before_refusal

        owner_audit = client.get("/v1/admin/audit", headers=owner_headers)
        assert owner_audit.status_code == 200, owner_audit.text
        assert SECRET_CANARY not in owner_audit.text
        for reader_headers in (codex_read_headers, hermes_read_headers):
            model_context = _assert_bootstrap_contains(
                client,
                reader_headers,
                query="Is there an operational credential for this task?",
                expected=None,
            )
            assert SECRET_CANARY not in json.dumps(model_context)

        # Neither read nor ordinary-capture principals can reach the
        # explicit mutation route. The exact writer has no read scope.
        correction_payload = {
            "kind": "correction",
            "content": CORRECTED_PREFERENCE,
            "supersedes": record_id,
            "explicit_user_statement": True,
        }
        forget_payload = {
            "kind": "context_forget",
            "content": "Explicit user forget request.",
            "supersedes": record_id,
            "explicit_user_statement": True,
        }
        for blocked_headers in (
            codex_read_headers,
            codex_capture_headers,
            hermes_read_headers,
            hermes_capture_headers,
        ):
            denied_correction = client.post(
                "/v1/ingestion/propose",
                headers=blocked_headers,
                json={**correction_payload, "idempotency_key": "denied-correction"},
            )
            denied_forget = client.post(
                "/v1/ingestion/propose",
                headers=blocked_headers,
                json={**forget_payload, "idempotency_key": "denied-forget"},
            )
            assert denied_correction.status_code == 403
            assert denied_forget.status_code == 403

        assert (
            client.get(f"/v1/context/{record_id}", headers=codex_write_headers).status_code == 403
        )

        corrected = client.post(
            "/v1/ingestion/propose",
            headers=codex_write_headers,
            json={**correction_payload, "idempotency_key": "codex-explicit-correction"},
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["disposition"] == "applied"
        assert corrected.json()["record_id"] == record_id
        assert corrected.json()["observation_origin"] == "ongoing_client"
        assert corrected.json()["explicit_user_statement"] is True

        for reader_headers in (codex_read_headers, hermes_read_headers):
            current = client.get(f"/v1/context/{record_id}", headers=reader_headers)
            assert current.status_code == 200, current.text
            assert current.json()["content"] == CORRECTED_PREFERENCE
            assert current.json()["explicit_user_statement"] is True
            assert current.json()["observation_origin"] == "ongoing_client"
            updated_bootstrap = _assert_bootstrap_contains(
                client,
                reader_headers,
                query="Use evidence-backed answers for the next generation.",
                expected=CORRECTED_PREFERENCE,
            )
            assert PREFERENCE not in [item["content"] for item in updated_bootstrap["items"]]

        truth = client.get(f"/v1/context/truth/{record_id}", headers=hermes_read_headers)
        assert truth.status_code == 200, truth.text
        evidence = truth.json()["evidence"]
        assert any(
            item["content"] == PREFERENCE
            and item["observation_origin"] == "live_user_evidence"
            and item["source_type"] == "client_capture_formation"
            for item in evidence
        )
        assert any(
            item["content"] == CORRECTED_PREFERENCE
            and item["observation_origin"] == "ongoing_client"
            and item["disposition"] == "applied"
            for item in evidence
        )

        forgotten = client.post(
            "/v1/ingestion/propose",
            headers=codex_write_headers,
            json={**forget_payload, "idempotency_key": "codex-explicit-forget"},
        )
        assert forgotten.status_code == 200, forgotten.text
        assert forgotten.json()["disposition"] == "applied"
        assert forgotten.json()["record_id"] == record_id
        assert forgotten.json()["observation_origin"] == "ongoing_client"
        assert forgotten.json()["explicit_user_statement"] is True

        for reader_headers in (codex_read_headers, hermes_read_headers):
            assert client.get(f"/v1/context/{record_id}", headers=reader_headers).status_code == 404
            after_forget = _assert_bootstrap_contains(
                client,
                reader_headers,
                query="Use evidence-backed answers for the next generation.",
                expected=None,
            )
            assert CORRECTED_PREFERENCE not in json.dumps(after_forget)

    # The explicit correction and forget remain closed after another restart.
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        for reader_headers in (codex_read_headers, hermes_read_headers):
            assert client.get(f"/v1/context/{record_id}", headers=reader_headers).status_code == 404
            final_bootstrap = _assert_bootstrap_contains(
                client,
                reader_headers,
                query="Resume the answer-writing task after the second restart.",
                expected=None,
            )
            assert PREFERENCE not in json.dumps(final_bootstrap)
            assert CORRECTED_PREFERENCE not in json.dumps(final_bootstrap)

        final_audit = client.get("/v1/admin/audit", headers=owner_headers)
        assert final_audit.status_code == 200, final_audit.text
        assert SECRET_CANARY not in final_audit.text

    export_path = tmp_path / "cross-client.atcexp"
    decrypted_path = tmp_path / "cross-client.zip"
    manifest = create_export(
        config.database_path,
        export_path,
        "cross-client-acceptance-passphrase",
        include_audit=True,
    )
    assert SECRET_CANARY not in json.dumps(manifest)
    _decrypt_file(export_path, decrypted_path, "cross-client-acceptance-passphrase")
    assert SECRET_CANARY.encode("utf-8") not in export_path.read_bytes()
    assert SECRET_CANARY.encode("utf-8") not in decrypted_path.read_bytes()
    _assert_canary_absent_from_tree(tmp_path, SECRET_CANARY)
