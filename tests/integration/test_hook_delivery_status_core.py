from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from allthecontext import claude_code_hook, codex_hook
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.hook_context import REFERENCE_FRAME
from allthecontext.http_client import ContextApiError
from allthecontext.models import ClientCreate
from fastapi.testclient import TestClient

MIXED_BUDGET_TURN = ("I prefer concise answers. " + "Production did not deploy. " * 40).strip()


def _event(content: str, event_id: str = "synthetic-status-budget") -> dict[str, Any]:
    return {
        "event_id": event_id,
        "idempotency_key": str(uuid.uuid4()),
        "session_id": "synthetic-status-session",
        "conversation_id": "synthetic-status-conversation",
        "sequence": 1,
        "role": "user",
        "content": content,
    }


def _headers(core: CoreService, scopes: list[str]) -> dict[str, str]:
    principal, token = core.store.create_client(
        ClientCreate(name="Synthetic status", scopes=scopes)
    )
    return {"Authorization": f"Bearer {token}", "X-ATC-Client-ID": principal.id}


def _bootstrap(
    client: TestClient, reader: dict[str, str], query: str, budget: int
) -> dict[str, Any]:
    response = client.post(
        "/v1/context/bootstrap",
        headers=reader,
        json={"query": query, "budget_chars": budget},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _render(payload: dict[str, Any], app: str, budget: int = 8000) -> list[dict[str, Any]]:
    text = (
        codex_hook._bounded_reference_context(payload, budget=budget)
        if app == "codex"
        else claude_code_hook._bounded_reference_context(payload)
    )
    assert len(text) <= (budget if app == "codex" else 8000)
    assert text.startswith(REFERENCE_FRAME)
    return [json.loads(part) for part in text.removeprefix(REFERENCE_FRAME).split("\n\n")]


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_cold_reader_capsule_with_empty_records_is_not_reported_as_absent_context(
    tmp_path: Path, app: str
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        owner = _headers(core, ["*"])
        for kind, content in [
            ("project", "Bounded synthetic project"),
            ("goal", "Capsule-only synthetic goal remains known."),
        ]:
            response = client.post(
                "/v1/ingestion/propose",
                headers=owner,
                json={
                    "kind": kind,
                    "content": content,
                    "scopes": ["project:status"],
                    "explicit_user_statement": True,
                },
            )
            assert response.status_code == 200, response.text
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        reader = _headers(cold, ["context:read"])
        payload = _bootstrap(
            client,
            reader,
            "Continue the Bounded synthetic project. Name the unrecorded operator.",
            8000,
        )
        assert payload["items"] == []
        assert payload["project_context"]["outcome"] == "activated"
        capsule = payload["project_context"]["capsule"]
        assert capsule["sections"]["current_goal"][0]["text"] == (
            "Capsule-only synthetic goal remains known."
        )
        rendered = _render(payload, app)
        project_items = [part for part in rendered if "atc_project" in part]
        assert any(
            part["project_content"] == "Capsule-only synthetic goal remains known."
            for part in project_items
        )
        assert all("atc_record" not in part for part in project_items)
        notice = rendered[-1]["atc_delivery"]
        assert notice["scope"] == "record_items"
        assert notice["state"] == "records_empty"
        assert any(
            part.get("atc_project_delivery", {}).get("state") == "items_returned"
            for part in rendered
        )
        assert "Bounded synthetic project" not in json.dumps(rendered)


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_core_budget_omission_and_complete_provenance_survive_cold_new_reader(
    tmp_path: Path, app: str
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    content = MIXED_BUDGET_TURN
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        # Delivery needs a long canonical fixture, not unsupported mixed-turn
        # automatic formation. Store imported text, then authorize its record
        # through the existing owner API. Capture-only authority cannot do so.
        source = core.store.add_source(
            content.encode("utf-8"),
            source_service="synthetic-delivery-fixture",
            source_type="text",
        )
        proposal = {
            "kind": "workflow",
            "content": content,
            "evidence": content,
            "source_id": source.id,
            "source_reference": "synthetic-budget-source:1",
        }
        writer = _headers(core, ["context:capture"])
        refused = client.post("/v1/ingestion/propose", headers=writer, json=proposal)
        assert refused.status_code == 403, refused.text
        owner = _headers(core, ["*"])
        proposed = client.post("/v1/ingestion/propose", headers=owner, json=proposal)
        assert proposed.status_code == 200, proposed.text
        candidate_id = proposed.json()["id"]
        assert (
            client.post(
                f"/v1/admin/candidates/{candidate_id}/approve", headers=writer, json={}
            ).status_code
            == 403
        )
        approved = client.post(
            f"/v1/admin/candidates/{candidate_id}/approve", headers=owner, json={}
        )
        assert approved.status_code == 200, approved.text
        record_id = approved.json()["id"]
        assert record_id is not None
        assert approved.json()["kind"] == "workflow"
        assert approved.json()["source_id"] == source.id
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        reader = _headers(cold, ["context:read"])
        small = _bootstrap(client, reader, "Production did not deploy", 256)
        assert small["items"] == []
        assert "budget" in small["pack_metadata"]["truncation_reasons"]
        omitted = _render(small, app, budget=256)
        assert omitted[-1]["atc_delivery"]["state"] == "budget_omitted"
        assert all("atc_record" not in part and "content" not in part for part in omitted)
        full = _render(_bootstrap(client, reader, "Production did not deploy", 8000), app)
        assert full[0]["content"] == content
        assert full[0]["atc_record"] == {"id": record_id, "version": 1}
        assert full[-1]["atc_delivery"]["state"] == "records_returned"
        truth = client.get("/v1/context/truth/" + record_id, headers=reader)
        assert truth.status_code == 200
        assert truth.json()["record"]["content"] == content
        assert any(item["content"] == content for item in truth.json()["evidence"])
        assert truth.json()["record"]["source_id"] == source.id
        assert truth.json()["source"]["id"] == source.id
        assert any(
            item["content"] == content and item["source_id"] == source.id
            for item in truth.json()["evidence"]
        )
        assert cold.store.get_source_content(source.id) == content.encode("utf-8")


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_atomic_capture_complete_provenance_survives_cold_new_reader(
    tmp_path: Path, app: str
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    content = "I prefer concise answers."
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        writer = _headers(core, ["context:capture"])
        response = client.post(
            "/v1/lifecycle/events", headers=writer, json=_event(content, "synthetic-status-atomic")
        )
        assert response.status_code == 200, response.text
        observation = core.store.get_observation(response.json()["observation_id"])
        assert observation.disposition.value == "applied"
        assert observation.observation_origin == "live_user_evidence"
        record_id = observation.record_id
        assert record_id is not None
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        reader = _headers(cold, ["context:read"])
        full = _render(_bootstrap(client, reader, "answer style", 8000), app)
        assert full[0]["content"] == content
        assert full[0]["atc_record"] == {"id": record_id, "version": 1}
        assert full[-1]["atc_delivery"]["state"] == "records_returned"
        truth = client.get("/v1/context/truth/" + record_id, headers=reader)
        assert truth.status_code == 200, truth.text
        assert truth.json()["record"]["content"] == content
        assert truth.json()["record"]["kind"] == "interaction_preference"
        assert truth.json()["record"]["attribute_key"] == "response_style"
        assert any(
            item["content"] == content and item["observation_origin"] == "live_user_evidence"
            for item in truth.json()["evidence"]
        )


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_original_mixed_budget_capture_remains_raw_after_replay_and_cold_reader(
    tmp_path: Path, app: str
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    payload = _event(MIXED_BUDGET_TURN)
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        writer = _headers(core, ["context:capture"])
        response = client.post("/v1/lifecycle/events", headers=writer, json=payload)
        assert response.status_code == 200, response.text
        receipt = response.json()
        original = core.store.get_observation(receipt["observation_id"])
        assert original.content == MIXED_BUDGET_TURN
        assert original.disposition.value == "tentative"
        assert original.record_id is None
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        replay = client.post("/v1/lifecycle/events", headers=writer, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "replayed"
        assert replay.json()["observation_id"] == receipt["observation_id"]
        assert cold.store.get_observation(receipt["observation_id"]) == original
        with cold.store.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 0
        reader = _headers(cold, ["context:read"])
        pack = _bootstrap(client, reader, "answer style and Production did not deploy", 8000)
        assert pack["items"] == []
        rendered = _render(pack, app)
        assert len(rendered) == 1
        assert rendered[0]["atc_delivery"]["state"] == "records_empty"
        assert "Production did not deploy" not in json.dumps(rendered)
        assert "concise" not in json.dumps(rendered)


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_real_http_permission_failure_is_unavailable_without_empty_memory_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: str
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        denied_reader = _headers(core, ["context:capture"])

        class HttpReader:
            def bootstrap_context_core_only(self, payload: dict[str, Any]) -> object:
                response = client.post("/v1/context/bootstrap", headers=denied_reader, json=payload)
                assert response.status_code == 403
                raise ContextApiError(403, "forbidden", "synthetic-private-error")

        module = codex_hook if app == "codex" else claude_code_hook
        monkeypatch.setattr(module, "_hook_client", lambda: HttpReader())
        text = (
            codex_hook._retrieve_hook_context("synthetic-private-query", 8000)
            if app == "codex"
            else claude_code_hook._retrieve_hook_context("synthetic-private-query")
        )
        notice = json.loads(text.removeprefix(REFERENCE_FRAME))["atc_delivery"]
        assert notice["state"] == "retrieval_unavailable"
        assert "synthetic-private" not in text
        assert "atc_record" not in text
