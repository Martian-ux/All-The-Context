"""Public synthetic capture/recall, with names read only from the bound anchor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from fastapi.testclient import TestClient

from tests.integration.test_decision_partial_runtime import capture, record, turn
from tests.integration.test_project_principal_bindings import Fixture, fixture

__all__ = ["fixture"]


def named_project(fixture: Fixture, name: object, key: str) -> str:
    # The label differs from scope, entity, content and query. Only this existing
    # structured field may condition named language; no mentioned name creates it.
    anchor = fixture.propose(
        "project",
        "An explicit registered project identity.",
        ["project:" + key],
        entity_key=key,
        attribute_key="identity",
        structured_value={"project_name": name},
    )
    response = fixture.client.post(
        "/v1/admin/project-identities", headers=fixture.owner, json={"anchor_record_id": anchor}
    )
    assert response.status_code == 200, response.text
    fixture.anchors[key] = anchor
    fixture.projects[key] = response.json()["canonical_project_id"]
    return key


def public_capture(fixture: Fixture, writer: dict[str, str], content: str) -> dict[str, Any]:
    return capture(fixture.client, writer, turn(content))


@pytest.mark.parametrize(
    "statement",
    [
        "I decided Quarry should use DuckDB for its offline cache.",
        "I have decided Quarry should use DuckDB for the offline cache.",
        "I've decided Quarry should use DuckDB for its offline cache.",
        "For Quarry, I choose DuckDB for the offline cache.",
        "I choose DuckDB for Quarry's offline cache.",
    ],
)
def test_named_choice_public_capture_cold_fresh_bound_reader(
    fixture: Fixture, statement: str
) -> None:
    key = named_project(fixture, "Quarry", "opaque-name-a")
    writer, binding = fixture.principal(key, ["context:capture"])
    receipt = public_capture(fixture, writer, statement)
    chosen = record(fixture, receipt)
    assert chosen.kind == "project_decision" and chosen.attribute_key == "offline_cache"
    assert chosen.structured_value is not None and chosen.structured_value["value"] == "DuckDB"
    assert chosen.entity_key == "project-decision:" + fixture.projects[key]
    assert chosen.scopes == ["project:" + key]
    assert receipt["project_binding"]["canonical_project_id"] == binding["canonical_project_id"]
    with (
        CoreService(fixture.core.config) as cold,
        TestClient(create_app(fixture.core.config, service=cold)) as client,
    ):
        restarted = Fixture(cold, client, fixture.owner, fixture.anchors, fixture.projects)
        reader, _ = restarted.principal(key, ["context:read"])
        pack = restarted.bootstrap(reader, "offline cache decision")
        items = [item for item in pack["items"] if item["id"] == chosen.id]
        assert len(items) == 1 and items[0]["content"] == statement
        assert items[0]["scopes"] == ["project:" + key]
        replay = capture(client, writer, turn(statement))
        # This is a fresh equivalent event; original-event replay is checked below.
        assert record(restarted, replay).id == chosen.id


def test_original_harness_binding_is_not_its_mentioned_name(fixture: Fixture) -> None:
    key = named_project(fixture, "Identical synthetic display label", "harbor")
    writer, binding = fixture.principal(key, ["context:capture"])
    reader, _ = fixture.principal(key, ["context:read"])
    response = fixture.client.get("/v1/context/project-binding", headers=reader)
    assert response.status_code == 200
    assert response.json()["canonical_project_id"] == binding["canonical_project_id"]
    statement = "I decided Harbor should use DuckDB for its offline cache."
    receipt = public_capture(fixture, writer, statement)
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    assert observation.content == statement and observation.record_id is None
    assert observation.disposition.value == "tentative"
    assert not any(
        item["content"] == statement
        for item in fixture.bootstrap(reader, "Harbor offline cache")["items"]
    )
    # Exact current-project language succeeds with that same authority, even
    # though neither name, scope suffix nor query can silently mint name authority.
    chosen = record(
        fixture, public_capture(fixture, writer, "For this project, use SQLite for storage.")
    )
    assert chosen.entity_key == "project-decision:" + binding["canonical_project_id"]


@pytest.mark.parametrize(
    "statement",
    [
        "I decided Foreign should use DuckDB for its offline cache.",
        "I didn't decide Quarry should use DuckDB for its offline cache.",
        "I decided Quarry should not use DuckDB for its offline cache.",
        "I might decide Quarry should use DuckDB for its offline cache.",
        '"I decided Quarry should use DuckDB for its offline cache."',
        "Alex said I decided Quarry should use DuckDB for its offline cache.",
        "I decided Quarry should use DuckDB for its offline cache, maybe.",
        "I decided Quarry should use DuckDB for its offline cache and Foreign should use SQLite.",
        "I decided Quarry should use DuckDB for its offline cache. I prefer concise answers.",
        "I decided last year Quarry should use DuckDB for its offline cache.",
        "Quarry uses DuckDB for its offline cache.",
        "I decided Quarry should use PostgreSQL for its offline cache.",
    ],
)
def test_unsupported_named_turns_stay_whole_tentative_evidence(
    fixture: Fixture, statement: str
) -> None:
    key = named_project(fixture, "Quarry", "named-negative")
    writer, _ = fixture.principal(key, ["context:capture"])
    receipt = public_capture(fixture, writer, statement)
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    assert observation.content == statement and observation.record_id is None
    assert observation.disposition.value == "tentative"


def test_same_display_name_never_selects_a_foreign_project_or_correction(fixture: Fixture) -> None:
    a = named_project(fixture, "Quarry", "name-collision-a")
    b = named_project(fixture, "Quarry", "name-collision-b")
    wa, _ = fixture.principal(a, ["context:capture"])
    wb, _ = fixture.principal(b, ["context:capture"])
    ra, _ = fixture.principal(a, ["context:read"])
    rb, _ = fixture.principal(b, ["context:read"])
    statement = "I decided Quarry should use DuckDB for its offline cache."
    first = record(fixture, public_capture(fixture, wa, statement))
    assert not any(
        item["id"] == first.id for item in fixture.bootstrap(rb, "Quarry offline cache")["items"]
    )
    correction = (
        "Correction: I decided Quarry should use SQLite for its offline cache instead of DuckDB."
    )
    foreign = public_capture(fixture, wb, correction)
    assert fixture.core.store.get_observation(foreign["observation_id"]).record_id is None
    second = record(fixture, public_capture(fixture, wb, statement))
    assert first.id != second.id and first.entity_key != second.entity_key
    assert second.id not in {item["id"] for item in fixture.bootstrap(ra, "Quarry")["items"]}


def test_named_corrections_exact_replay_aba_and_source_history(fixture: Fixture) -> None:
    key = named_project(fixture, "Quarry", "named-correction")
    writer, _ = fixture.principal(key, ["context:capture"])
    second, _ = fixture.principal(key, ["context:capture"])
    start = datetime.now(UTC) - timedelta(hours=2)
    first_event = turn(
        "I decided Quarry should use DuckDB for its offline cache.", observed_at=start
    )
    first_receipt = capture(fixture.client, writer, first_event)
    first = record(fixture, first_receipt)
    correction = (
        "Correction: I decided Quarry should use SQLite for its offline cache instead of DuckDB."
    )
    event = turn(correction, observed_at=start + timedelta(minutes=2))
    changed_receipt = capture(fixture.client, second, event)
    changed = record(fixture, changed_receipt)
    assert changed.id == first.id and changed.version == 2
    assert changed.source_reference != first.source_reference
    replay = capture(fixture.client, second, event)
    assert (
        replay["status"] == "replayed"
        and replay["observation_id"] == changed_receipt["observation_id"]
    )
    back = record(
        fixture,
        capture(
            fixture.client,
            writer,
            turn(
                "Correction: I decided Quarry should use DuckDB for its offline cache "
                "instead of SQLite.",
                observed_at=start + timedelta(minutes=4),
            ),
        ),
    )
    assert back.version == 3 and back.id == first.id
    stale = capture(
        fixture.client, second, turn(correction, observed_at=start + timedelta(minutes=1))
    )
    observation = fixture.core.store.get_observation(stale["observation_id"])
    assert (
        observation.record_id is None
        and observation.decision_reason == "project_decision_stale_correction"
    )
    assert fixture.core.store.get_record(first.id) == back
    assert len(fixture.core.store.record_history(first.id)) == 3
    reader, _ = fixture.principal(key, ["context:read"])
    recalled = fixture.bootstrap(reader, "offline cache")
    assert any(
        item["id"] == first.id and item["content"] == back.content for item in recalled["items"]
    )


@pytest.mark.parametrize(
    "name", [None, {}, "", "Quarry or Foreign", "Maybe Quarry", "Quarry\n", "Quarry / Foreign"]
)
def test_missing_or_unsafe_anchor_name_is_not_inferred(fixture: Fixture, name: object) -> None:
    key = named_project(fixture, name, "no-name")
    writer, _ = fixture.principal(key, ["context:capture"])
    receipt = public_capture(
        fixture, writer, "I decided Quarry should use DuckDB for its offline cache."
    )
    assert fixture.core.store.get_observation(receipt["observation_id"]).record_id is None


def test_unbound_writer_cannot_use_a_registered_name(fixture: Fixture) -> None:
    named_project(fixture, "Quarry", "unbound-name")
    writer, _ = fixture.principal(None, ["context:capture"])
    receipt = public_capture(
        fixture, writer, "I decided Quarry should use DuckDB for its offline cache."
    )
    assert fixture.core.store.get_observation(receipt["observation_id"]).record_id is None


def test_anchor_rename_is_not_alias_authority_and_original_replay_is_stable(
    fixture: Fixture,
) -> None:
    key = named_project(fixture, "Quarry", "rename-bound-name")
    writer, binding = fixture.principal(key, ["context:capture"])
    first_event = turn("I decided Quarry should use DuckDB for its offline cache.")
    receipt = capture(fixture.client, writer, first_event)
    first = record(fixture, receipt)
    changed = fixture.client.post(
        "/v1/admin/records/" + fixture.anchors[key] + "/correct",
        headers=fixture.owner,
        json={
            "content": "An explicit registered project identity.",
            "structured_value": {"project_name": "Renamed Quarry"},
            "reason": "Synthetic current display-name change",
        },
    )
    assert changed.status_code == 200, changed.text
    reader, _ = fixture.principal(key, ["context:read"])
    resolved = fixture.client.get("/v1/context/project-binding", headers=reader)
    assert resolved.status_code == 200
    assert resolved.json()["canonical_project_id"] == binding["canonical_project_id"]
    correction = "Correction: For Renamed Quarry, use SQLite for offline cache instead of DuckDB."
    rejected = public_capture(fixture, writer, correction)
    observation = fixture.core.store.get_observation(rejected["observation_id"])
    assert (
        observation.record_id is None
        and observation.decision_reason == "project_decision_lineage_mismatch"
    )
    replay = capture(fixture.client, writer, first_event)
    assert replay["status"] == "replayed" and replay["observation_id"] == receipt["observation_id"]
    assert fixture.core.store.get_record(first.id) == first
    assert any(
        item["id"] == first.id for item in fixture.bootstrap(reader, "offline cache")["items"]
    )
