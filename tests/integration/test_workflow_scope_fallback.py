"""Disposable public captures: scope refusal, provenance, replay and cold readers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from fastapi.testclient import TestClient

from tests.integration.test_bound_named_project_decisions import named_project
from tests.integration.test_decision_partial_runtime import capture, record, turn
from tests.integration.test_project_principal_bindings import Fixture


@pytest.fixture
def lab(tmp_path: Path) -> Iterator[Fixture]:
    config = CoreConfig.in_directory(tmp_path / "vault", require_auth=True)
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        setup = client.post("/v1/setup", json={"name": "Synthetic scope repair owner"})
        assert setup.status_code == 200, setup.text
        fixture = Fixture(
            core, client, {"Authorization": "Bearer " + setup.json()["token"]}, {}, {}
        )
        named_project(fixture, "Juniper", "scope-one")
        named_project(fixture, "Rook", "scope-two")
        yield fixture


@pytest.mark.parametrize("project", [None, "scope-one"])
@pytest.mark.parametrize(
    "content",
    [
        "We use DuckDB for Juniper.",
        "We use PostgreSQL for Rook.",
        "My stack is Python in Juniper.",
    ],
)
def test_qualification_keeps_only_exact_raw_evidence(
    lab: Fixture, project: str | None, content: str
) -> None:
    writer, _ = lab.principal(project, ["context:capture"])
    payload = turn(content)
    receipt = capture(lab.client, writer, payload)
    observation = lab.core.store.get_observation(receipt["observation_id"])
    assert observation.record_id is None and observation.disposition.value == "tentative"
    assert observation.content == content and observation.kind == "captured_user_turn"
    assert observation.source_type == "client_capture"
    assert observation.observation_origin == "client_capture"
    assert observation.source_reference is not None
    assert observation.source_reference.startswith("client-capture-event-")
    assert observation.scopes == (["project:" + project] if project else [])
    replay = capture(lab.client, writer, payload)
    assert replay["status"] == "replayed"
    assert replay["observation_id"] == receipt["observation_id"]
    assert replay["capture_event_id"] == receipt["capture_event_id"]
    candidates, _ = lab.core.store.list_candidates(status=None, limit=100)
    assert len([item for item in candidates if item.content == content]) == 1


def test_separate_qualifications_cannot_replace_global_workflow_after_rebuild(lab: Fixture) -> None:
    writer, _ = lab.principal(None, ["context:capture"])
    global_text = "We use Python."
    global_record = record(lab, capture(lab.client, writer, turn(global_text)))
    assert global_record.scopes == [] and global_record.entity_key == "user"
    inputs = []
    for content in ("We use DuckDB for Juniper.", "We use PostgreSQL for Rook."):
        other_writer, _ = lab.principal(None, ["context:capture"])
        payload = turn(content)
        receipt = capture(lab.client, other_writer, payload)
        observation = lab.core.store.get_observation(receipt["observation_id"])
        assert observation.record_id is None and observation.content == content
        inputs.append((other_writer, payload, receipt))
    assert lab.core.store.get_record(global_record.id) == global_record
    with (
        CoreService(lab.core.config) as cold,
        TestClient(create_app(lab.core.config, service=cold)) as client,
    ):
        cold.store.rebuild_integrity_groups()
        reopened = Fixture(cold, client, lab.owner, lab.anchors, lab.projects)
        reader, _ = reopened.principal(None, ["context:read"])
        foreign_reader, _ = reopened.principal("scope-two", ["context:read"])
        for headers, bound in ((reader, False), (foreign_reader, True)):
            items = reopened.bootstrap(headers)["items"]
            workflows = [item for item in items if item["kind"] == "workflow"]
            if bound:
                # Existing bound selection admits only its canonical project.
                assert workflows == []
            else:
                assert {item["id"] for item in workflows} == {global_record.id}
                assert workflows[0]["content"] == global_text and workflows[0]["version"] == 1
            assert not any(
                item["content"] == payload["content"] for item in items for _, payload, _ in inputs
            )
        assert cold.store.get_record(global_record.id) == global_record
        for headers, payload, receipt in inputs:
            replay = capture(client, headers, payload)
            assert replay["status"] == "replayed"
            assert replay["observation_id"] == receipt["observation_id"]
            observation = cold.store.get_observation(receipt["observation_id"])
            assert observation.record_id is None and observation.content == payload["content"]
        truth = client.get("/v1/context/truth/" + global_record.id, headers=reader)
        assert truth.status_code == 200, truth.text
        assert truth.json()["history_count"] == 1
        assert all(item["content"] == global_text for item in truth.json()["evidence"])


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("We use Python for all projects.", "workflow"),
        ("My workflow is terminal based across every project.", "workflow"),
        ("I prefer tabs for code indentation.", "interaction_preference"),
    ],
)
def test_explicit_global_claim_keeps_provenance_for_fresh_unbound_reader(
    lab: Fixture, content: str, kind: str
) -> None:
    writer, _ = lab.principal(None, ["context:capture"])
    payload = turn(content)
    receipt = capture(lab.client, writer, payload)
    formed = record(lab, receipt)
    assert formed.kind == kind and formed.scopes == [] and formed.content == content
    assert formed.source_type == "client_capture_formation"
    assert formed.observation_origin == "live_user_evidence"
    with (
        CoreService(lab.core.config) as cold,
        TestClient(create_app(lab.core.config, service=cold)) as client,
    ):
        reopened = Fixture(cold, client, lab.owner, lab.anchors, lab.projects)
        reader, _ = reopened.principal(None, ["context:read"])
        assert formed.id in {item["id"] for item in reopened.bootstrap(reader)["items"]}
        truth = client.get("/v1/context/truth/" + formed.id, headers=reader)
        assert truth.status_code == 200, truth.text
        assert truth.json()["record"]["source_reference"] == formed.source_reference
        assert any(item["content"] == content for item in truth.json()["evidence"])
        replay = capture(client, writer, payload)
        assert (
            replay["status"] == "replayed" and replay["observation_id"] == receipt["observation_id"]
        )
        assert cold.store.get_record(formed.id).version == 1


def test_bound_named_and_current_project_decisions_keep_separate_scopes(lab: Fixture) -> None:
    choices = []
    for project, content in (
        ("scope-one", "For Juniper, use DuckDB for the database."),
        ("scope-two", "For this project, use PostgreSQL for the database."),
    ):
        writer, _ = lab.principal(project, ["context:capture"])
        chosen = record(lab, capture(lab.client, writer, turn(content)))
        assert chosen.kind == "project_decision" and chosen.content == content
        assert chosen.scopes == ["project:" + project]
        assert chosen.entity_key == "project-decision:" + lab.projects[project]
        choices.append((project, chosen))
    assert choices[0][1].id != choices[1][1].id
    with (
        CoreService(lab.core.config) as cold,
        TestClient(create_app(lab.core.config, service=cold)) as client,
    ):
        reopened = Fixture(cold, client, lab.owner, lab.anchors, lab.projects)
        for project, chosen in choices:
            reader, _ = reopened.principal(project, ["context:read"])
            pack = reopened.bootstrap(reader, "database decision")
            returned = {item["id"] for item in pack["items"]}
            assert chosen.id in returned
            assert all(other.id not in returned for _, other in choices if other.id != chosen.id)
            assert all(item["scopes"] == chosen.scopes for item in pack["items"])
            truth = client.get("/v1/context/truth/" + chosen.id, headers=reader)
            assert truth.status_code == 200, truth.text
            assert any(item["content"] == chosen.content for item in truth.json()["evidence"])
