"""Synthetic correction regressions, independent of the frozen evaluator.

Only direct, same-facet corrections may replace the current value. Full source
wording remains evidence; temporal framing does not become preference content.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from allthecontext import claude_code_hook, codex_hook
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.hook_context import REFERENCE_FRAME
from allthecontext.memory_policy import extract_live_user_claim
from allthecontext.models import ClientCreate
from fastapi.testclient import TestClient

from tests.integration.test_personal_preference_contract import _records, core

__all__ = ["core"]
CoreFixture = tuple[CoreService, TestClient, dict[str, str], dict[str, str]]
INITIAL = "I prefer quick responses."
CHANGED = "Correction: I prefer thorough responses now, rather than quick responses."


def source(content: str, at: datetime, *, role: str = "user") -> dict[str, Any]:
    return {
        "event_id": "correction-" + uuid.uuid4().hex,
        "idempotency_key": str(uuid.uuid4()),
        "session_id": "synthetic-correction",
        "conversation_id": "synthetic-correction",
        "sequence": 1,
        "role": role,
        "content": content,
        "observed_at": at.isoformat(),
    }


def capture(client: TestClient, writer: dict[str, str], event: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/lifecycle/events", headers=writer, json=event)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True, response.text
    return dict(response.json())


@pytest.mark.parametrize("prefix", ["", "Correction: ", "Please correct my preference: "])
@pytest.mark.parametrize("contrast", ["rather than", "instead of"])
@pytest.mark.parametrize("comma", [",", ""])
def test_terminal_now_is_framing_only_in_complete_same_facet_correction(
    prefix: str, contrast: str, comma: str
) -> None:
    content = f"{prefix}I prefer thorough responses now{comma} {contrast} quick responses."
    claim = extract_live_user_claim(content)
    assert claim is not None
    assert claim.content == content
    assert claim.attribute_key == "response_style"
    assert claim.value == "thorough responses"
    assert claim.structured_value["expected_old_value"] == "quick responses"


@pytest.mark.parametrize(
    "content",
    [
        "Correction: I prefer thorough responses now, rather than quick responses for this task.",
        "Correction: I prefer thorough responses now, rather than quick responses?",
        "Correction: I prefer thorough responses for now, rather than quick responses.",
        "Correction: I prefer thorough responses only now, rather than quick responses.",
        "Correction: I prefer thorough responses right now, rather than quick responses.",
        "Correction: I prefer thorough responses now, rather than English replies.",
        "Correction: I prefer thorough responses now, rather than paragraph answers.",
        "Correction: I prefer thorough responses now, rather than herbal tea.",
        "Correction: I prefer thorough responses now, rather than quick responses and dark mode.",
        "Correction: I prefer thorough responses now, rather than quick responses. Ship it.",
        "Correction: I do not prefer thorough responses now, rather than quick responses.",
        "Correction: I prefer not-thorough responses now, rather than quick responses.",
        "Correction: I prefer maybe thorough responses now, rather than quick responses.",
        "She said: I prefer thorough responses now, rather than quick responses.",
        '"Correction: I prefer thorough responses now, rather than quick responses."',
        "I prefer thorough responses now.",
    ],
)
def test_qualifications_cross_facets_quotes_and_mixed_turns_remain_raw(content: str) -> None:
    assert extract_live_user_claim(content) is None


@pytest.mark.parametrize("timing", ["equal", "older", "future"])
def test_non_newer_or_future_correction_does_not_replace_current_value(
    core: CoreFixture, timing: str
) -> None:
    service, client, writer, reader = core
    first_at = datetime.now(UTC) - timedelta(hours=2)
    first = capture(client, writer, source(INITIAL, first_at))
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    before = service.store.get_record(record_id)
    at = {
        "equal": first_at,
        "older": first_at - timedelta(seconds=1),
        "future": datetime.now(UTC) + timedelta(days=1),
    }[timing]
    # Existing supported grammar isolates the chronology defect from new syntax.
    event = source("Correction: I prefer thorough responses instead of quick responses.", at)
    refused = capture(client, writer, event)
    observation = service.store.get_observation(refused["observation_id"])
    assert observation.disposition.value == ("ignored" if timing == "older" else "tentative")
    if timing != "older":
        assert observation.decision_reason == "personal_preference_stale"
        assert observation.record_id is None
    assert service.store.get_record(record_id) == before
    assert len(service.store.record_history(record_id)) == 1
    assert [item["content"] for item in _records(client, reader)] == [INITIAL]
    assert capture(client, writer, event)["status"] == "replayed"
    assert service.store.get_record(record_id) == before


def test_delayed_aba_correction_cannot_reuse_returned_old_value(core: CoreFixture) -> None:
    service, client, writer, reader = core
    start = datetime.now(UTC) - timedelta(hours=3)
    first = capture(client, writer, source(INITIAL, start))
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    for offset, content in [
        (2, "Correction: I prefer thorough responses instead of quick responses."),
        (3, "Correction: I prefer quick responses instead of thorough responses."),
    ]:
        receipt = capture(client, writer, source(content, start + timedelta(minutes=offset)))
        assert service.store.get_observation(receipt["observation_id"]).record_id == record_id
    before = service.store.get_record(record_id)
    assert before.version == 3
    event = source(
        "Correction: I prefer precise responses instead of quick responses.",
        start + timedelta(minutes=1),
    )
    refused = capture(client, writer, event)
    assert service.store.get_observation(refused["observation_id"]).disposition.value == "ignored"
    assert service.store.get_record(record_id) == before
    assert [item["id"] for item in _records(client, reader)] == [record_id]


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_natural_correction_survives_close_rebuild_new_reader_replay_and_forget(
    core: CoreFixture, app: str
) -> None:
    service, client, writer, _reader = core
    start = datetime.now(UTC) - timedelta(hours=2)
    initial = source(INITIAL, start)
    first = capture(client, writer, initial)
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    second_principal, token = service.store.create_client(
        ClientCreate(name="Other synthetic capture app", scopes=["context:capture"])
    )
    other_writer = {"Authorization": "Bearer " + token, "X-ATC-Client-ID": second_principal.id}
    changed_at = start + timedelta(minutes=1)
    if app == "claude":
        changed_at = changed_at.astimezone(timezone(timedelta(hours=5, minutes=30)))
    changed_event = source(CHANGED, changed_at)
    changed = capture(client, other_writer, changed_event)
    current = service.store.get_record(record_id)
    assert current.content == CHANGED and current.version == 2
    assert current.structured_value is not None
    assert current.structured_value["value"] == "thorough responses"
    reference = current.source_reference
    client.close()
    service.close()
    with (
        CoreService(service.config) as cold,
        TestClient(create_app(service.config, service=cold)) as reopened,
    ):
        cold.store.rebuild_integrity_groups()
        principal, token = cold.store.create_client(
            ClientCreate(name="Fresh synthetic reader", scopes=["context:read"])
        )
        fresh_reader = {"Authorization": "Bearer " + token, "X-ATC-Client-ID": principal.id}
        pack = reopened.post(
            "/v1/context/bootstrap",
            headers=fresh_reader,
            json={"task_description": "answer style", "character_budget": 8000},
        )
        assert pack.status_code == 200, pack.text
        payload = pack.json()
        assert {item["content"] for item in payload["items"]} == {CHANGED}
        assert payload["items"][0]["source_reference"] == reference
        text = (
            codex_hook._bounded_reference_context(payload, budget=8000)
            if app == "codex"
            else claude_code_hook._bounded_reference_context(payload)
        )
        blocks = [json.loads(part) for part in text.removeprefix(REFERENCE_FRAME).split("\n\n")]
        records = [part for part in blocks if "atc_record" in part]
        assert len(records) == 1
        assert records[0]["content"] == CHANGED
        assert records[0]["atc_record"] == {"id": record_id, "version": 2}
        truth = reopened.get("/v1/context/truth/" + record_id, headers=fresh_reader)
        assert truth.status_code == 200, truth.text
        assert truth.json()["record"]["source_reference"] == reference
        assert {item["content"] for item in truth.json()["evidence"]} == {INITIAL, CHANGED}
        assert [
            version["snapshot"]["content"] for version in cold.store.record_history(record_id)
        ] == [INITIAL, CHANGED]
        for headers, event, receipt in [
            (writer, initial, first),
            (other_writer, changed_event, changed),
        ]:
            replay = capture(reopened, headers, event)
            assert replay["status"] == "replayed"
            assert replay["observation_id"] == receipt["observation_id"]
        assert cold.store.get_record(record_id).version == 2
        cold.store.delete_record(record_id, reason="Synthetic explicit forget")
        cold.store.rebuild_integrity_groups()
        capture(reopened, other_writer, changed_event)
        refused = capture(reopened, other_writer, source(CHANGED, start + timedelta(minutes=2)))
        assert cold.store.get_observation(refused["observation_id"]).record_id is None
        assert _records(reopened, fresh_reader) == []
        assert cold.store.get_record(record_id, include_deleted=True).deleted_at is not None
