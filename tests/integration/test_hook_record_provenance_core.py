from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from allthecontext import claude_code_hook, codex_hook
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.hook_context import REFERENCE_FRAME
from allthecontext.models import ClientCreate
from fastapi.testclient import TestClient

from bench.memory_acceptance import Event
from bench.memory_acceptance_atc import AtcAdapter


def _headers(service: CoreService, name: str, scopes: list[str]) -> dict[str, str]:
    principal, token = service.store.create_client(ClientCreate(name=name, scopes=scopes))
    return {"Authorization": f"Bearer {token}", "X-ATC-Client-ID": principal.id}


def _event(content: str, sequence: int) -> dict:
    return {
        "event_id": f"synthetic-style-{sequence}",
        "idempotency_key": str(uuid.uuid4()),
        "session_id": "synthetic-session",
        "conversation_id": "synthetic-conversation",
        "sequence": sequence,
        "role": "user",
        "content": content,
    }


def _bootstrap(client: TestClient, headers: dict[str, str], query: str = "answer style") -> dict:
    response = client.post(
        "/v1/context/bootstrap", headers=headers, json={"query": query, "budget_chars": 8000}
    )
    assert response.status_code == 200
    return response.json()


def _render(payload: dict, app: str) -> list[dict]:
    text = (
        codex_hook._bounded_reference_context(payload, budget=8000)
        if app == "codex"
        else claude_code_hook._bounded_reference_context(payload)
    )
    assert text.startswith(REFERENCE_FRAME)
    blocks = [json.loads(block) for block in text.removeprefix(REFERENCE_FRAME).split("\n\n")]
    return [block for block in blocks if "content" in block]


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_cold_core_delivered_reference_resolves_current_persisted_evidence(
    tmp_path: Path, app: str
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    corrected = "I now prefer thorough explanations."
    with CoreService(config) as service:
        writer = _headers(service, "Synthetic capture", ["context:capture"])
        reader = _headers(service, "Synthetic reader", ["context:read"])
        with TestClient(create_app(config, service=service)) as client:
            for event in [_event("I prefer concise answers.", 1), _event(corrected, 2)]:
                response = client.post("/v1/lifecycle/events", headers=writer, json=event)
                assert response.status_code == 200

    # No adapter event/reference caches survive. Both context and evidence come
    # from a fresh Core and authenticated HTTP reads of the synthetic vault.
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        blocks = _render(_bootstrap(client, reader), app)
        assert len(blocks) == 1
        block = blocks[0]
        assert block["content"] == corrected
        reference = block["atc_record"]
        assert reference["version"] == 2
        route = "/v1/context/truth/" + reference["id"]
        result = client.get(route, headers=reader)
        assert result.status_code == 200
        truth = result.json()
        record = truth["record"]
        assert record["version"] == reference["version"]
        assert record["content"] == block["content"]
        current_evidence = [
            evidence
            for evidence in truth["evidence"]
            if evidence["source_reference"] == record["source_reference"]
            and evidence["content"] == corrected
        ]
        assert len(current_evidence) == 1
        assert current_evidence[0]["observation_origin"] == "live_user_evidence"
        assert any(e["content"] == "I prefer concise answers." for e in truth["evidence"])

        # A record reference is versioned, not an immutable source quote.
        response = client.post(
            "/v1/lifecycle/events",
            headers=writer,
            json=_event("I now prefer brief answers.", 3),
        )
        assert response.status_code == 200
        replacement = client.get(route, headers=reader).json()["record"]
        assert replacement["version"] > reference["version"]
        assert replacement["content"] != block["content"]


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_deleted_reference_stays_unreadable_after_cold_restart_and_original_replay(
    tmp_path: Path, app: str
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    event = _event("I prefer answers in bullet points.", 1)
    with CoreService(config) as service:
        writer = _headers(service, "Synthetic capture", ["context:capture"])
        reader = _headers(service, "Synthetic reader", ["context:read"])
        owner = _headers(service, "Synthetic owner", ["*"])
        with TestClient(create_app(config, service=service)) as client:
            assert (
                client.post("/v1/lifecycle/events", headers=writer, json=event).status_code == 200
            )
            reference = _render(_bootstrap(client, reader), app)[0]["atc_record"]
            deleted = client.post(
                "/v1/ingestion/forget",
                headers=owner,
                json={"record_id": reference["id"], "reason": "Synthetic forget"},
            )
            assert deleted.status_code == 200

    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        replay = client.post("/v1/lifecycle/events", headers=writer, json=event)
        assert replay.status_code == 200
        assert replay.json()["status"] == "replayed"
        resolved = client.get("/v1/context/truth/" + reference["id"], headers=reader)
        assert resolved.status_code == 404
        for question in ["What layout should replies use?", "How should answers be presented?"]:
            payload = _bootstrap(client, reader, question)
            assert payload["items"] == []
            text = (
                codex_hook._bounded_reference_context(payload, budget=8000)
                if app == "codex"
                else claude_code_hook._bounded_reference_context(payload)
            )
            notice = json.loads(text.removeprefix(REFERENCE_FRAME))["atc_delivery"]
            assert notice["scope"] == "record_items"
            assert notice["state"] == "records_empty"
            assert "atc_record" not in text


def test_adapter_does_not_invent_current_citations_for_stale_or_deleted_references(
    tmp_path: Path,
) -> None:
    adapter = AtcAdapter()
    adapter.reset(tmp_path, "supplemental-reference-lifecycle")
    try:
        event = Event(
            event_id="supplemental-initial",
            at="2026-10-02T03:00:00Z",
            app="codex",
            role="user",
            text="I prefer concise answers.",
        )
        assert adapter.capture(event).status == "ok"
        reader = adapter._new_client("Synthetic supplemental reader", ["context:read"])
        payload = adapter._call(
            "POST", "/v1/context/bootstrap", {"query": "answer style"}, headers=reader
        )
        delivered = codex_hook._bounded_reference_context(payload, budget=8000)
        assert adapter._delivered_chunks(delivered, headers=reader)[0].citations

        correction = event.model_copy(
            update={
                "event_id": "supplemental-current",
                "at": "2026-10-02T03:01:00Z",
                "text": "I now prefer detailed explanations.",
            }
        )
        assert adapter.capture(correction).status == "ok"
        stale = adapter._delivered_chunks(delivered, headers=reader)
        assert stale[0].text == event.text
        assert stale[0].citations == ()

        assert adapter.control("forget_event", event.event_id).status == "ok"
        deleted = adapter._delivered_chunks(delivered, headers=reader)
        assert deleted[0].text == event.text
        assert deleted[0].citations == ()
        assert adapter.core is not None
        adapter.core.store.revoke_client(reader["X-ATC-Client-ID"])
        with pytest.raises(RuntimeError, match="HTTP 401"):
            adapter._delivered_chunks(delivered, headers=reader)
    finally:
        adapter.close()
