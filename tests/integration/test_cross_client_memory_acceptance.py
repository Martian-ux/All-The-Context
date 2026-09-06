from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.export import _decrypt_file, create_export
from allthecontext.models import CandidateInput
from allthecontext.security import WITNESS_EXPLICIT_USER_STATEMENT
from fastapi.testclient import TestClient

from bench.cross_client_reader_outcomes import (
    ARM_IDS,
    TASK_IDS,
    TASK_PROMPTS,
    build_packet,
    build_reader_result,
    context_items_from_bootstrap,
    grade_six_cells,
    maintain_context_file,
)

PREFERENCE = "I prefer concise answers."
CORRECTED_PREFERENCE = "I prefer evidence-backed answers."
SECRET_CANARY = "ATC_COMPOSED_SECRET_CANARY_6N4P8W"


def test_frozen_project_handoff_from_external_manifest(tmp_path: Path) -> None:
    """Replay frozen current records/query offline; never invoke a reader.

    The manager supplies read-only inputs externally, not as repository fixtures.
    Historical construction is covered separately by lifecycle acceptance tests.
    """
    directory = os.environ.get("ATC_FROZEN_INPUT_DIR")
    if directory is None:
        pytest.skip("manager-owned frozen inputs were not supplied")
    root = Path(directory)
    for name, expected in (
        (
            "reader-packet-bundle.json",
            "929157cebcc435a153a97d41ebf65ee87afa7414c2bfa2017e9ad5337f8aee3c",
        ),
        (
            "evaluator-manifest.json",
            "10aa6618b080b341ed3f3311d1e917156ee75d66f5f371e5e4f55a2c89608a49",
        ),
    ):
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    manifest = json.loads((root / "evaluator-manifest.json").read_text(encoding="utf-8"))
    config = CoreConfig.in_directory(tmp_path, require_auth=True)

    async def replay(service: CoreService) -> None:
        transport = httpx.ASGITransport(app=create_app(config, service=service))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            setup = await client.post(
                "/v1/setup", json={"name": "Frozen replay owner", "scopes": []},
            )
            assert setup.status_code == 200
            owner = _bearer(str(setup.json()["token"]))
            created = await client.post(
                "/v1/admin/clients", headers=owner,
                json={"name": "Frozen replay reader", "scopes": ["context:read"]},
            )
            assert created.status_code == 200
            reader = _bearer(str(created.json()["token"]))
            for record in manifest["records_after_construction"]:
                candidate = service.store.add_candidate(CandidateInput(
                    **{key: record[key] for key in (
                        "kind", "content", "entity_key", "attribute_key", "scopes",
                        "source_service", "source_type", "explicit_user_statement",
                    )},
                    structured_value={"project_name": "Borealis"}, confidence=1.0,
                ))
                service.store.approve_candidate(candidate.id)
            response = await client.post(
                "/v1/context/bootstrap", headers=reader, json=manifest["bootstrap_request"],
            )
            assert response.status_code == 200
            payload = response.json()
            contents = {item["content"] for item in payload["items"]}
            required = {
                record["content"] for record in manifest["records_after_construction"]
                if record["attribute_key"] in {
                    "deployment_region", "production_blocker", "next_action",
                }
            }
            assert required <= contents
            assert payload["context_mode"] == "local_core"
            assert payload["project_context"]["reason"] == "explicit_project_match"
            assert payload["total_used_chars"] <= manifest["bootstrap_request"]["character_budget"]
            print(json.dumps({
                "http_status": response.status_code,
                "context_mode": payload["context_mode"],
                "project_reason": payload["project_context"]["reason"],
                "required_fact_count": len(required & contents),
                "selected_count": len(contents),
                "total_used_chars": payload["total_used_chars"],
                "pack_metadata": payload["pack_metadata"],
                "reader_calls": 0,
            }, sort_keys=True))

    with CoreService(config) as service:
        asyncio.run(replay(service))


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


def test_reader_outcome_bridge_exports_six_cells_from_real_core_bootstrap(
    tmp_path: Path,
) -> None:
    """Export a bounded packet matrix after proving correction through real Core state."""

    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        setup = client.post("/v1/setup", json={"name": "Reader bridge owner", "scopes": []})
        assert setup.status_code == 200, setup.text
        owner_headers = _bearer(str(setup.json()["token"]))
        capture_id, capture_headers = _create_client(
            client,
            owner_headers,
            name="Reader bridge capture",
            scopes=["context:capture"],
        )
        _read_id, read_headers = _create_client(
            client,
            owner_headers,
            name="Reader bridge read",
            scopes=["context:read"],
        )
        _write_id, write_headers = _create_client(
            client,
            owner_headers,
            name="Reader bridge correction",
            scopes=["context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        )

        _capture_event(
            client,
            client_id=capture_id,
            headers=capture_headers,
            event_id="reader-bridge-initial",
            idempotency_key=_fixed_v4_key(30),
            sequence=1,
            role="user",
            content=PREFERENCE,
        )
        records = _fetch_rows(
            config.database_path,
            "SELECT id,content,explicit_user_statement,observation_origin "
            "FROM context_records WHERE deleted_at IS NULL",
        )
        assert len(records) == 1
        record_id = str(records[0]["id"])
        assert records[0]["content"] == PREFERENCE

        corrected = client.post(
            "/v1/ingestion/propose",
            headers=write_headers,
            json={
                "kind": "correction",
                "content": CORRECTED_PREFERENCE,
                "supersedes": record_id,
                "explicit_user_statement": True,
                "idempotency_key": "reader-bridge-correction",
            },
        )
        assert corrected.status_code == 200, corrected.text
        correction_payload = corrected.json()
        assert corrected.json()["record_id"] == record_id
        assert corrected.json()["content"] == CORRECTED_PREFERENCE
        assert correction_payload["explicit_user_statement"] is True
        correction_observation_id = str(correction_payload["id"])

        current = client.get(f"/v1/context/{record_id}", headers=read_headers)
        assert current.status_code == 200, current.text
        assert current.json()["content"] == CORRECTED_PREFERENCE
        truth = client.get(f"/v1/context/truth/{record_id}", headers=read_headers)
        assert truth.status_code == 200, truth.text
        evidence = truth.json()["evidence"]
        assert truth.json()["record"]["id"] == record_id
        assert truth.json()["record"]["content"] == CORRECTED_PREFERENCE
        assert [item["content"] for item in evidence] == [PREFERENCE, CORRECTED_PREFERENCE]
        assert evidence[1]["observation_id"] == correction_observation_id
        assert evidence[1]["observation_origin"] == "ongoing_client"

        capture_rows = _fetch_rows(
            config.database_path,
            "SELECT id,provider_event_id,provider_item_id,generation,order_key,operation,"
            "normalized_payload_json,status FROM capture_events WHERE provider_event_id=?",
            ("reader-bridge-initial",),
        )
        assert len(capture_rows) == 1
        capture_row = capture_rows[0]
        assert capture_row["status"] == "applied"
        assert capture_row["provider_event_id"] == "reader-bridge-initial"
        assert capture_row["provider_item_id"] == "reader-bridge-initial"
        assert capture_row["generation"] == 0
        assert capture_row["order_key"] == "1"
        assert capture_row["operation"] == "upsert"
        capture_payload = json.loads(str(capture_row["normalized_payload_json"]))
        assert capture_payload["role"] == "user"
        assert capture_payload["content"] == PREFERENCE

        candidate_rows = _fetch_rows(
            config.database_path,
            "SELECT id,kind,content,source_type,observation_origin,explicit_user_statement,"
            "record_id,capture_event_id,observed_at,created_at,supersedes "
            "FROM context_candidates WHERE record_id=? ORDER BY observed_at,created_at,id",
            (record_id,),
        )
        assert [str(row["id"]) for row in candidate_rows] == [
            str(item["observation_id"]) for item in evidence
        ]
        assert [row["content"] for row in candidate_rows] == [PREFERENCE, CORRECTED_PREFERENCE]
        assert [bool(row["explicit_user_statement"]) for row in candidate_rows] == [False, True]
        assert candidate_rows[0]["kind"] == "interaction_preference"
        assert candidate_rows[0]["source_type"] == "client_capture_formation"
        assert candidate_rows[0]["capture_event_id"] == capture_row["id"]
        assert candidate_rows[0]["record_id"] == record_id
        assert candidate_rows[1]["kind"] == "correction"
        assert candidate_rows[1]["record_id"] == record_id
        assert candidate_rows[1]["supersedes"] == record_id
        assert candidate_rows[1]["capture_event_id"] is None
        assert candidate_rows[1]["observation_origin"] == "ongoing_client"

        maintained_events = [
            {
                "slot": "response_style",
                "sequence": index,
                "content": str(row["content"]),
                "explicit_user_statement": bool(row["explicit_user_statement"]),
            }
            for index, row in enumerate(candidate_rows, start=1)
        ]
        maintained = maintain_context_file(maintained_events)
        assert maintained.query_blind is True
        assert maintained.items == (CORRECTED_PREFERENCE,)
        assert maintained.construction_units == len(candidate_rows) == 2

        positive_bootstrap = _assert_bootstrap_contains(
            client,
            read_headers,
            query=TASK_PROMPTS["changed_preference"].instruction,
            expected=CORRECTED_PREFERENCE,
        )
        negative_bootstrap = _assert_bootstrap_contains(
            client,
            read_headers,
            query=TASK_PROMPTS["unknown_deployment_region"].instruction,
            expected=None,
        )
        positive_items = context_items_from_bootstrap(positive_bootstrap)
        negative_items = context_items_from_bootstrap(negative_bootstrap)
        assert CORRECTED_PREFERENCE in positive_items
        assert PREFERENCE not in positive_items
        assert any(
            item["id"] == record_id
            and item["content"] == CORRECTED_PREFERENCE
            and item["observation_origin"] == "ongoing_client"
            for item in positive_bootstrap["items"]
        )

    packets = {}
    for task_key in TASK_IDS:
        packets[(ARM_IDS[0], task_key)] = build_packet(
            TASK_PROMPTS[task_key],
            (),
            context_source="no_memory",
            construction_units=0,
            access_units=0,
            retrieval_units=0,
        )
        packets[(ARM_IDS[1], task_key)] = build_packet(
            TASK_PROMPTS[task_key],
            maintained.items,
            context_source="maintained_context_file",
            construction_units=0,
            access_units=0,
            retrieval_units=0,
        )
        atc_items = positive_items if task_key == TASK_IDS[0] else negative_items
        packets[(ARM_IDS[2], task_key)] = build_packet(
            TASK_PROMPTS[task_key],
            atc_items,
            context_source="actual_atc_bootstrap",
            construction_units=0,
            access_units=1,
            retrieval_units=1,
        )

    results = {
        (ARM_IDS[0], "changed_preference"): build_reader_result(
            packets[(ARM_IDS[0], "changed_preference")],
            "UNKNOWN",
            provenance={"source": "canned_test_output"},
        ),
        (ARM_IDS[0], "unknown_deployment_region"): build_reader_result(
            packets[(ARM_IDS[0], "unknown_deployment_region")],
            "UNKNOWN",
            provenance={"source": "canned_test_output"},
        ),
        (ARM_IDS[1], "changed_preference"): build_reader_result(
            packets[(ARM_IDS[1], "changed_preference")],
            CORRECTED_PREFERENCE,
            provenance={"source": "canned_test_output"},
        ),
        (ARM_IDS[1], "unknown_deployment_region"): build_reader_result(
            packets[(ARM_IDS[1], "unknown_deployment_region")],
            "UNKNOWN",
            provenance={"source": "canned_test_output"},
        ),
        (ARM_IDS[2], "changed_preference"): build_reader_result(
            packets[(ARM_IDS[2], "changed_preference")],
            CORRECTED_PREFERENCE,
            provenance={"source": "canned_test_output"},
        ),
        (ARM_IDS[2], "unknown_deployment_region"): build_reader_result(
            packets[(ARM_IDS[2], "unknown_deployment_region")],
            "UNKNOWN",
            provenance={"source": "canned_test_output"},
        ),
    }
    report = grade_six_cells(
        packets,
        results,
        construction_units_by_arm={
            ARM_IDS[0]: 0,
            ARM_IDS[1]: maintained.construction_units,
            ARM_IDS[2]: 0,
        },
        stale_preference=PREFERENCE,
        irrelevant_context_by_task={"unknown_deployment_region": [CORRECTED_PREFERENCE]},
    )
    assert report["denominators"] == {
        "cell_count": 6,
        "task_count": 2,
        "arm_count": 3,
        "success_count": 5,
    }
    assert report["classification_counts"]["safe_abstention"] == 1
    assert report["model_execution_by_harness"] is False
    assert all(len(outcome["packet_sha256"]) == 64 for outcome in report["outcomes"])
    assert all(outcome["evidence_kind"] == "non_model_fixture" for outcome in report["outcomes"])
    assert report["construction_access_retrieval_accounting"][ARM_IDS[0]]["construction_units"] == 0
    assert report["construction_access_retrieval_accounting"][ARM_IDS[1]]["construction_units"] == 2
