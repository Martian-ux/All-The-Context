"""Authentic synthetic capture receipts and the integrated bounded policy."""

from __future__ import annotations

import json
from typing import Any

import pytest
from allthecontext.models import ObservationDisposition
from allthecontext.project_binding import ProjectBindings
from allthecontext.project_decision_policy import (
    ProjectDecisionAbstention,
    ProjectDecisionPlan,
    plan_project_decision,
)
from allthecontext.security import ClientPrincipal

from tests.integration.test_project_principal_bindings import Fixture, event, fixture

__all__ = ["fixture"]


def captured_plan(
    fixture: Fixture,
    capture_receipt: dict[str, Any],
    client_id: str,
) -> ProjectDecisionPlan | ProjectDecisionAbstention:
    """Model the parent transaction's data inputs without admitting a decision."""

    store = fixture.core.store
    with store.connect() as connection:
        principal = store._policy_principal_tx(
            connection,
            # Resolve durable authority, not payload or display-name hints.
            ClientPrincipal(client_id, "", frozenset()),
        )
        assert principal is not None
        context = ProjectBindings(store).resolve_tx(connection, principal)
        row = connection.execute(
            "SELECT normalized_payload_json FROM capture_events WHERE id=?",
            (capture_receipt["capture_event_id"],),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        assert context is not None and payload["project_binding"] == context.receipt()
        return plan_project_decision(
            payload["content"], role=payload["role"], project_context=context
        )


def test_authenticated_same_words_route_to_distinct_canonical_plans_and_admission(
    fixture: Fixture,
) -> None:
    content = "For this project, use SQLite for storage."
    plans = []
    for project in fixture.projects:
        writer, binding = fixture.principal(project, ["context:capture"])
        reader, _ = fixture.principal(project, ["context:read"])
        payload = event(content)
        response = fixture.client.post("/v1/lifecycle/events", headers=writer, json=payload)
        assert response.status_code == 200, response.text
        receipt = response.json()
        decision = captured_plan(fixture, receipt, binding["client_id"])
        assert isinstance(decision, ProjectDecisionPlan)
        assert decision.project_context.canonical_project_id == fixture.projects[project]
        assert decision.claim.content == content
        assert decision.scopes == ("project:" + project,)
        plans.append(decision)
        observation = fixture.core.store.get_observation(receipt["observation_id"])
        assert observation.kind == "project_decision"
        assert observation.content == content
        assert observation.disposition is ObservationDisposition.APPLIED
        assert observation.record_id is not None
        assert any(
            item["id"] == observation.record_id and item["kind"] == "project_decision"
            for item in fixture.bootstrap(reader, "storage")["items"]
        )
        with fixture.core.store.connect() as connection:
            raw = connection.execute(
                "SELECT content,disposition,record_id FROM context_candidates "
                "WHERE capture_event_id=? AND source_type='client_capture'",
                (receipt["capture_event_id"],),
            ).fetchone()
        assert tuple(raw) == (content, "tentative", None)
        replay = fixture.client.post("/v1/lifecycle/events", headers=writer, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "replayed"
        assert replay.json()["observation_id"] == receipt["observation_id"]
        assert captured_plan(fixture, replay.json(), binding["client_id"]) == decision
    assert plans[0].entity_key != plans[1].entity_key


@pytest.mark.parametrize(
    "content",
    [
        "For this project, do not use SQLite for storage.",
        "For this project, maybe use SQLite for storage.",
        "Alice said: For this project, use SQLite for storage.",
        "For this project, use SQLite for storage. I prefer concise answers.",
        "For the other project, use SQLite for storage.",
        "For this project, I decided to use SQLite for storage last year.",
    ],
)
def test_capture_keeps_the_whole_abstained_turn_and_exact_origin(
    fixture: Fixture, content: str
) -> None:
    project = next(iter(fixture.projects))
    writer, binding = fixture.principal(project, ["context:capture"])
    response = fixture.client.post("/v1/lifecycle/events", headers=writer, json=event(content))
    assert response.status_code == 200, response.text
    assert captured_plan(fixture, response.json(), binding["client_id"]) is (
        ProjectDecisionAbstention.UNSUPPORTED
    )
    observation = fixture.core.store.get_observation(response.json()["observation_id"])
    assert observation.content == content
    assert observation.record_id is None
    with fixture.core.store.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT normalized_payload_json FROM capture_events WHERE id=?",
                (response.json()["capture_event_id"],),
            ).fetchone()[0]
        )
    assert payload["content"] == content
    assert payload["project_binding"]["canonical_project_id"] == fixture.projects[project]
