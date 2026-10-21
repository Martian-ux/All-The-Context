"""The 16 independently reviewed desired-contract cases plus bounded regressions.

The original assertions come from evidence-only 988ff07d. No frozen acceptance
fixture, grader or oracle is imported. Additional cases cover actual capture
transactions, ordinary authority, compatibility boundaries and lifecycle.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.memory_policy import extract_live_user_claim
from allthecontext.models import CandidateInput, ClientCreate
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("content", "value"),
    [
        ("I prefer replies arranged as a bulleted list.", "replies arranged as a bulleted list"),
        ("My preference is responses with numbered steps.", "responses with numbered steps"),
        ("I like numbered summaries.", "numbered summaries"),
        ("I prefer meticulous replies.", "meticulous replies"),
        ("I prefer herbal tea.", "herbal tea"),
        ("I prefer expanded walkthroughs.", "expanded walkthroughs"),
    ],
)
def test_complete_positive_values_are_not_replaced_by_a_value_whitelist(
    content: str, value: str
) -> None:
    claim = extract_live_user_claim(content)
    assert claim is not None, "Complete, direct personal preference was lost"
    assert claim.kind == "interaction_preference"
    assert claim.content == content
    assert claim.value == value


@pytest.mark.parametrize(
    "content",
    [
        "I prefer those summaries.",
        "I do not prefer compact summaries.",
        "I prefer compact summaries for Project Harbor.",
        "I prefer compact summaries and dark mode.",
        "I prefer probably-compact summaries.",
        "I prefer my colleague's summaries.",
        "She said: I prefer compact summaries.",
        '"I prefer compact summaries."',
    ],
)
def test_qualified_or_unbound_turn_is_not_an_unqualified_positive_claim(content: str) -> None:
    assert extract_live_user_claim(content) is None


@pytest.fixture
def core(
    tmp_path: Path,
) -> Iterator[tuple[CoreService, TestClient, dict[str, str], dict[str, str]]]:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        headers = []
        for scope in ("context:capture", "context:read"):
            principal, token = service.store.create_client(ClientCreate(name=scope, scopes=[scope]))
            headers.append({"Authorization": f"Bearer {token}", "X-ATC-Client-ID": principal.id})
        yield service, client, headers[0], headers[1]


def _capture(
    client: TestClient,
    headers: dict[str, str],
    sequence: int,
    content: str,
    *,
    hour: int | None = None,
    role: str = "user",
) -> dict[str, Any]:
    response = client.post(
        "/v1/lifecycle/events",
        headers=headers,
        json={
            "event_id": f"recovery-paraphrase-{sequence}",
            "idempotency_key": str(uuid.UUID(f"00000000-0000-4000-8000-{sequence:012d}")),
            "session_id": "synthetic-recovery-session",
            "conversation_id": "synthetic-recovery-conversation",
            "sequence": sequence,
            "role": role,
            "content": content,
            "observed_at": f"2026-10-02T{hour if hour is not None else 10 + sequence:02d}:00:00Z",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    return dict(response.json())


def _records(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.post(
        "/v1/context/search", headers=headers, json={"kinds": ["interaction_preference"]}
    )
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def test_unrelated_preferences_coexist_instead_of_replacing_a_generic_slot(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, capture_headers, read_headers = core
    drink = "I prefer herbal tea."
    reply = "I prefer responses with numbered steps."
    first = _capture(client, capture_headers, 1, drink)
    first_id = service.store.get_observation(first["observation_id"]).record_id
    assert first_id is not None, "Initial complete personal preference was not formed"
    initial = service.store.get_record(first_id)
    assert initial.kind == "interaction_preference"
    assert initial.entity_key == "user"
    assert initial.content == drink
    initial_slot = f"{initial.kind}/{initial.entity_key}/{initial.attribute_key}"
    _capture(client, capture_headers, 2, reply)
    current = service.store.get_record(first_id)
    assert current.content == drink, (
        "Unrelated later preference replaced the first record in slot "
        f"{initial_slot} (initial value {initial.structured_value!r})"
    )
    assert current.version == 1
    assert {record["content"] for record in _records(client, read_headers)} == {drink, reply}


def test_natural_correction_keeps_full_evidence_and_rejects_a_stale_predecessor(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, capture_headers, read_headers = core
    original = "I prefer quick responses."
    correction = (
        "Please correct my preference: I prefer thorough responses instead of quick responses."
    )
    first = _capture(client, capture_headers, 1, original)
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    corrected = _capture(client, capture_headers, 2, correction)
    record = service.store.get_record(record_id)
    assert record.content == correction, "The natural explicit correction was ignored"
    assert record.version == 2
    assert record.structured_value is not None
    assert record.structured_value["value"] == "thorough responses"
    assert service.store.get_observation(corrected["observation_id"]).record_id == record_id
    truth = client.get(f"/v1/context/truth/{record_id}", headers=read_headers)
    assert truth.status_code == 200, truth.text
    assert any(
        item["content"] == correction
        and item["observation_id"] == corrected["observation_id"]
        and item["observation_origin"] == "live_user_evidence"
        for item in truth.json()["evidence"]
    )
    assert any(
        item["content"] == original and item["observation_id"] == first["observation_id"]
        for item in truth.json()["evidence"]
    )
    assert [
        version["snapshot"]["content"] for version in service.store.record_history(record_id)
    ] == [
        original,
        correction,
    ]
    stale = "Please correct my preference: I prefer brief responses instead of quick responses."
    refused = _capture(client, capture_headers, 3, stale)
    observation = service.store.get_observation(refused["observation_id"])
    assert observation.disposition.value in {"tentative", "ignored"}
    assert [item["id"] for item in _records(client, read_headers)] == [record_id]
    assert service.store.get_record(record_id).content == correction
    assert service.store.get_record(record_id).version == 2
    assert len(service.store.record_history(record_id)) == 2


@pytest.mark.parametrize(
    ("content", "facet"),
    [
        ("I like lucid replies.", "response_style"),
        ("My preference is source-linked walkthroughs.", "response_style"),
        ("I prefer explanations with alphabetical lists.", "response_layout"),
        ("I prefer responses arranged as an ordered list.", "response_layout"),
        ("I prefer bullet-point answers.", "response_layout"),
        ("I prefer bullet point replies.", "response_layout"),
        ("I prefer green tea.", "beverage"),
        ("I now prefer cold coffee.", "beverage"),
        ("I prefer tabs for code indentation.", "response_format"),
        ("I prefer light theme.", "interface_theme"),
    ],
)
def test_independent_complete_subject_paraphrases(content: str, facet: str) -> None:
    claim = extract_live_user_claim(content)
    assert claim is not None
    assert claim.attribute_key == facet
    assert claim.content == content


@pytest.mark.parametrize(
    "content",
    [
        "I prefer lucid replies when debugging.",
        "I prefer lucid replies for Project Harbor.",
        "I prefer probably-lucid replies.",
        "I prefer previous walkthroughs.",
        "I prefer another tea.",
        "I prefer their green tea.",
        "I dislike lucid replies.",
        "I do not prefer green tea.",
        "I prefer lucid replies?",
        "I prefer lucid replies...",
        "I prefer precise numbered replies.",
        "I prefer JSON replies.",
        "I prefer table-formatted replies.",
        "I prefer step-by-step explanations.",
        "I prefer green tea and ordered replies.",
        "We prefer green tea.",
        "I prefer hiking trails.",
        "Correction: I prefer green tea instead of lucid replies.",
        "Please correct my preference: I prefer lucid replies instead of those replies.",
    ],
)
def test_unsupported_scope_reference_polarity_or_cross_facet_stays_raw(content: str) -> None:
    assert extract_live_user_claim(content) is None


def test_distinct_facets_and_open_values_keep_independent_personal_records(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, writer, reader = core
    inputs = [
        "I like precise explanations.",
        "I prefer explanations with ordered steps.",
        "I prefer spaces for code indentation.",
        "I prefer dark mode.",
        "I prefer sparkling water.",
    ]
    receipts = [_capture(client, writer, i, text) for i, text in enumerate(inputs, 1)]
    ids = [service.store.get_observation(item["observation_id"]).record_id for item in receipts]
    assert None not in ids and len(set(ids)) == 5
    assert {item["content"] for item in _records(client, reader)} == set(inputs)
    correction = "My preference has changed: I prefer flat water instead of sparkling water."
    result = _capture(client, writer, 6, correction)
    assert service.store.get_observation(result["observation_id"]).record_id == ids[4]
    assert {item["content"] for item in _records(client, reader)} == {*inputs[:4], correction}


def test_delayed_correction_keeps_ordinary_timestamp_authority_and_replay(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, writer, reader = core
    first = _capture(client, writer, 1, "I prefer precise explanations.", hour=13)
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    correction = "Correction: I prefer lucid explanations rather than precise explanations."
    delayed = _capture(client, writer, 2, correction, hour=12)
    observed = service.store.get_observation(delayed["observation_id"])
    assert observed.kind == "interaction_preference"
    assert observed.explicit_user_statement is False
    assert observed.disposition.value == "ignored"
    assert service.store.get_record(record_id).version == 1
    accepted = _capture(client, writer, 3, correction, hour=14)
    formed = service.store.get_observation(accepted["observation_id"])
    assert formed.record_id == record_id
    assert formed.structured_value is not None
    assert formed.structured_value["expected_record_id"] == record_id
    assert formed.structured_value["expected_record_version"] == "1"
    assert _capture(client, writer, 1, "I prefer precise explanations.", hour=13)["status"] == (
        "replayed"
    )
    assert _capture(client, writer, 3, correction, hour=14)["status"] == "replayed"
    assert [item["content"] for item in _records(client, reader)] == [correction]
    assert service.store.get_record(record_id).version == 2
    assert len(service.store.record_history(record_id)) == 2


def test_concurrent_corrections_compare_the_predecessor_under_one_write_transaction(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, writer, reader = core
    first = _capture(client, writer, 1, "I prefer precise explanations.")
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    barrier = Barrier(2)
    corrections = [
        "Correction: I prefer lucid explanations instead of precise explanations.",
        "Correction: I prefer accessible explanations instead of precise explanations.",
    ]

    def capture(index: int) -> dict[str, Any]:
        barrier.wait(timeout=10)
        return _capture(client, writer, index + 2, corrections[index])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(capture, range(2)))
    observations = [service.store.get_observation(item["observation_id"]) for item in results]
    assert sorted(item.disposition.value for item in observations) == ["applied", "tentative"]
    assert len(_records(client, reader)) == 1
    assert service.store.get_record(record_id).content in corrections
    assert service.store.get_record(record_id).version == 2
    assert len(service.store.record_history(record_id)) == 2
    for index, result in enumerate(results):
        assert (
            _capture(client, writer, index + 2, corrections[index])["observation_id"]
            == (result["observation_id"])
        )
    assert service.store.get_record(record_id).version == 2


@pytest.mark.parametrize("boundary", ["kind", "scope", "denied", "explicit"])
def test_capture_does_not_overwrite_a_different_boundary_or_escalate_authority(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]], boundary: str
) -> None:
    service, client, writer, _reader = core
    candidate = CandidateInput(
        kind="workflow" if boundary == "kind" else "interaction_preference",
        entity_key="user",
        attribute_key="response_style",
        content="I prefer precise explanations.",
        structured_value={"value": "precise explanations"},
        scopes=["project.harbor"] if boundary == "scope" else [],
        denied_clients=[writer["X-ATC-Client-ID"]] if boundary == "denied" else [],
        explicit_user_statement=boundary == "explicit",
        observed_at="2026-10-02T10:00:00Z",
    )
    seeded = service.store.add_candidate(candidate)
    # Synthetic pre-existing canonical state, not production capture approval.
    original = (
        service.store.get_record(seeded.record_id)
        if seeded.record_id is not None
        else service.store.approve_candidate(seeded.id)
    )
    result = _capture(
        client,
        writer,
        1,
        "Correction: I prefer lucid explanations instead of precise explanations.",
    )
    observation = service.store.get_observation(result["observation_id"])
    assert observation.disposition.value in {"tentative", "ignored"}
    assert service.store.get_record(original.id).content == original.content
    assert service.store.get_record(original.id).version == original.version
    with service.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_records WHERE deleted_at IS NULL"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("deleted", [False, True])
def test_legacy_generic_records_are_preserved_without_automatic_migration(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
    deleted: bool,
) -> None:
    service, client, writer, reader = core
    seeded = service.store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            entity_key="user",
            attribute_key="preference",
            content="I prefer herbal tea.",
            structured_value={"value": "herbal tea"},
        )
    )
    legacy = service.store.approve_candidate(seeded.id)
    if deleted:
        service.store.delete_record(legacy.id, reason="Synthetic legacy deletion")
    prior_version = service.store.get_record(legacy.id, include_deleted=True).version
    for sequence, content in enumerate(
        (
            "I prefer cold coffee.",
            "I prefer replies with numbered steps.",
            "I prefer lucid replies.",
        ),
        1,
    ):
        result = _capture(client, writer, sequence, content)
        observation = service.store.get_observation(result["observation_id"])
        if sequence == 1:
            assert observation.disposition.value == "tentative"
            assert observation.decision_reason == "personal_preference_legacy"
            assert observation.record_id is None
        else:
            assert observation.disposition.value == "applied"
            assert observation.record_id is not None
    current = _records(client, reader)
    assert {item["attribute_key"] for item in current} == (
        {"response_layout", "response_style"}
        if deleted
        else {"preference", "response_layout", "response_style"}
    )
    assert service.store.get_record(legacy.id, include_deleted=True).version == prior_version


def test_existing_typed_facet_can_update_without_reinterpreting_legacy_generic(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, writer, reader = core
    first = _capture(client, writer, 1, "I prefer precise explanations.")
    typed_id = service.store.get_observation(first["observation_id"]).record_id
    assert typed_id is not None
    seeded = service.store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            entity_key="user",
            attribute_key="preference",
            content="I prefer herbal tea.",
            structured_value={"value": "herbal tea"},
        )
    )
    legacy = service.store.approve_candidate(seeded.id)
    correction = "Correction: I prefer lucid explanations instead of precise explanations."
    result = _capture(client, writer, 2, correction)
    assert service.store.get_observation(result["observation_id"]).record_id == typed_id
    assert service.store.get_record(typed_id).version == 2
    assert {item["content"] for item in _records(client, reader)} == {legacy.content, correction}
    assert service.store.get_record(legacy.id).version == 1


@pytest.mark.parametrize("layout", ["bullet-point answers", "bullet point answers"])
def test_layout_modifier_does_not_share_a_slot_with_style(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]], layout: str
) -> None:
    service, client, writer, reader = core
    layout_content = f"I prefer {layout}."
    first = _capture(client, writer, 1, layout_content)
    layout_id = service.store.get_observation(first["observation_id"]).record_id
    assert layout_id is not None
    style = "I prefer precise explanations."
    _capture(client, writer, 2, style)
    assert {item["content"] for item in _records(client, reader)} == {layout_content, style}
    assert service.store.get_record(layout_id).version == 1


def test_compound_style_layout_or_unmodelled_encoding_does_not_replace_known_facets(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, writer, reader = core
    style = "I prefer precise explanations."
    layout = "I prefer numbered replies."
    originals = [_capture(client, writer, i, text) for i, text in enumerate((style, layout), 1)]
    for sequence, content in enumerate(
        (
            "I prefer precise numbered replies.",
            "I prefer JSON replies.",
            "I prefer table-formatted replies.",
            "I prefer step-by-step explanations.",
        ),
        3,
    ):
        result = _capture(client, writer, sequence, content)
        assert service.store.get_observation(result["observation_id"]).record_id is None
    assert {item["content"] for item in _records(client, reader)} == {style, layout}
    for result in originals:
        record_id = service.store.get_observation(result["observation_id"]).record_id
        assert record_id is not None
        assert service.store.get_record(record_id).version == 1


def test_equivalent_ordinary_turn_from_another_client_can_reinforce_delayed_evidence(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, writer, reader = core
    content = "I prefer precise explanations."
    first = _capture(client, writer, 1, content, hour=14)
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    principal, token = service.store.create_client(
        ClientCreate(name="Second synthetic capture app", scopes=["context:capture"])
    )
    second_writer = {"Authorization": f"Bearer {token}", "X-ATC-Client-ID": principal.id}
    second = _capture(client, second_writer, 2, content, hour=13)
    observation = service.store.get_observation(second["observation_id"])
    assert observation.disposition.value == "reinforced"
    assert observation.record_id == record_id
    assert observation.explicit_user_statement is False
    assert [item["id"] for item in _records(client, reader)] == [record_id]
    assert service.store.get_record(record_id).content == content


def test_other_roles_and_unprivileged_api_payloads_cannot_claim_personal_truth(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
) -> None:
    service, client, writer, reader = core
    first = _capture(client, writer, 1, "I prefer precise explanations.")
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    for sequence, role in enumerate(("assistant", "tool", "imported"), 2):
        result = _capture(client, writer, sequence, "I prefer lucid explanations.", role=role)
        assert service.store.get_observation(result["observation_id"]).record_id is None
    payload = {
        "event_id": "unprivileged",
        "idempotency_key": str(uuid.uuid4()),
        "session_id": "synthetic",
        "conversation_id": "synthetic",
        "sequence": 5,
        "role": "user",
        "content": "I prefer lucid explanations.",
    }
    assert client.post("/v1/lifecycle/events", headers=reader, json=payload).status_code == 403
    for field, value in (("expected_record_id", record_id), ("explicit_user_statement", True)):
        response = client.post(
            "/v1/lifecycle/events", headers=writer, json={**payload, field: value}
        )
        assert response.status_code == 422
    assert service.store.get_record(record_id).version == 1


def test_deleted_predecessor_cannot_be_corrected_or_reformed_after_restart(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    refused_ids: list[str] = []
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        headers = []
        for scope in ("context:capture", "context:read", "*"):
            principal, token = service.store.create_client(ClientCreate(name=scope, scopes=[scope]))
            headers.append({"Authorization": f"Bearer {token}", "X-ATC-Client-ID": principal.id})
        writer, reader, owner = headers
        first = _capture(client, writer, 1, "I prefer precise explanations.")
        record_id = service.store.get_observation(first["observation_id"]).record_id
        assert record_id is not None
        response = client.post(
            "/v1/ingestion/forget",
            headers=owner,
            json={"record_id": record_id, "reason": "Synthetic deletion"},
        )
        assert response.status_code == 200, response.text
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        assert _capture(client, writer, 1, "I prefer precise explanations.")["status"] == "replayed"
        for sequence, content in enumerate(
            (
                "Correction: I prefer lucid explanations instead of precise explanations.",
                "I prefer accessible explanations.",
            ),
            2,
        ):
            result = _capture(client, writer, sequence, content)
            refused_ids.append(result["observation_id"])
            observation = cold.store.get_observation(result["observation_id"])
            assert observation.disposition.value == "tentative"
            assert observation.record_id is None
        assert _records(client, reader) == []
        assert client.get(f"/v1/context/truth/{record_id}", headers=reader).status_code == 404
        assert cold.store.get_record(record_id, include_deleted=True).deleted_at is not None
    with CoreService(config) as reopened:
        assert [reopened.store.get_observation(item).decision_reason for item in refused_ids] == [
            "personal_preference_slot_blocked",
            "personal_preference_slot_blocked",
        ]
