"""Public first reads expose partial mentions without creating project membership.

All data is fictional development evidence. These are in-process HTTP/MCP and
Core recreation journeys; the native acceptance probe owns OS-process restart.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from allthecontext import http_client, mcp_adapter
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.http_client import ContextHttpClient
from allthecontext.project_runtime import ProjectRuntimeError
from allthecontext.security import WITNESS_EXPLICIT_USER_STATEMENT
from fastapi.testclient import TestClient

PROJECT = "Silver Finch"
TASKS = [
    "Write the short visitor blurb for Silver Finch, including where and when to come "
    "and how to get in. Only advertise confirmed arrangements.",
    "Make the Silver Finch front desk card with current visitor logistics, entrant "
    "counting, listening equipment, and skippable work.",
    "I am taking the last shift on Silver Finch. Retrieve the closeout handoff for "
    "tonight: what to do with the count, which work is already finished, and what "
    "promise can be made to the sponsor.",
]


def _post(
    client: TestClient, headers: dict[str, str], path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _client(client: TestClient, owner: dict[str, str], name: str) -> tuple[str, dict[str, str]]:
    result = _post(client, owner, "/v1/admin/clients", {"name": name, "scopes": ["context:read"]})
    return result["client"]["id"], {"Authorization": f"Bearer {result['token']}"}


def _setup(client: TestClient) -> tuple[dict[str, str], str, dict[str, str]]:
    result = _post(client, {}, "/v1/setup", {"name": "Fictional owner", "scopes": []})
    owner = {"Authorization": f"Bearer {result['token']}"}
    reader_id, reader = _client(client, owner, "Fresh fictional reader")
    return owner, reader_id, reader


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, str], str, dict[str, str]]]:
    config = CoreConfig.in_directory(tmp_path / "fictional-vault", require_auth=True)
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        owner, reader_id, reader = _setup(client)
        yield client, owner, reader_id, reader


def _save(
    client: TestClient, owner: dict[str, str], content: str, **changes: Any
) -> dict[str, Any]:
    reference = hashlib.sha256(content.encode()).hexdigest()[:16]
    observation = _post(
        client,
        owner,
        "/v1/ingestion/propose",
        {
            "kind": "project_decision",
            "content": content,
            "scopes": ["project"],
            "explicit_user_statement": True,
            "source_reference": f"fictional:first-read:{reference}",
            "evidence": content,
            **changes,
        },
    )
    if observation["disposition"] == "tentative":
        # Real owner approval also supports deliberate low-confidence/conflict fixtures.
        return _post(
            client,
            owner,
            f"/v1/admin/candidates/{observation['id']}/approve",
            {"reason": "Approve this fictional development fixture."},
        )
    assert observation["disposition"] == "applied", observation
    truth = client.get(f"/v1/admin/memory-truth/{observation['record_id']}", headers=owner)
    assert truth.status_code == 200, truth.text
    return truth.json()["record"]


def _bootstrap(client: TestClient, reader: dict[str, str], **changes: Any) -> dict[str, Any]:
    return _post(
        client,
        reader,
        "/v1/context/bootstrap",
        {
            "task_description": TASKS[0],
            "current_project": PROJECT,
            "character_budget": 4000,
            **changes,
        },
    )


def _evidence_ids(result: dict[str, Any]) -> set[str]:
    return {item["id"] for item in result.get("project_search_evidence", {}).get("items", [])}


def _mcp_first_read(
    client: TestClient, reader_id: str, reader: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    # Keep SDK tools and HTTP-client forwarding/decoding real; replace only
    # network transport with the public in-process HTTP endpoint.
    def request(method: str, url: str, **kwargs: Any) -> Any:
        kwargs.pop("timeout", None)
        kwargs.pop("trust_env", None)
        return client.request(method, url, **kwargs)

    monkeypatch.setattr(http_client.httpx, "request", request)
    monkeypatch.setattr(
        mcp_adapter,
        "_client",
        lambda: ContextHttpClient(
            "http://testserver", reader_id, reader["Authorization"].removeprefix("Bearer ")
        ),
    )
    server = mcp_adapter.build_mcp()
    call = anyio.run(
        server.call_tool,
        "bootstrap_context",
        {"task_description": TASKS[0], "current_project": PROJECT},
    )
    assert call.structured_content is not None
    return call.structured_content


@pytest.mark.parametrize("task", TASKS)
def test_named_first_read_returns_useful_partial_evidence_without_activation(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]], task: str
) -> None:
    client, owner, _, reader = world
    facts = [
        _save(client, owner, "Silver Finch visitors use the Elm Hall entrance at 17:20."),
        _save(client, owner, "Silver Finch counting uses a paper tally because radio failed."),
        _save(client, owner, "Silver Finch has three loan receivers.", kind="project"),
        _save(client, owner, "Silver Finch print proof is finished; skip another review."),
        _save(client, owner, "Silver Finch sponsor streaming remains undecided.", kind="project"),
    ]
    preference = _save(client, owner, "Use concise answers.", kind="interaction_preference")
    before = client.get("/v1/admin/projects", headers=owner).json()
    result = _bootstrap(client, reader, task_description=task)
    # The original complete-anchor contract and mandatory preference tier survive.
    assert [item["id"] for item in result["items"]] == [preference["id"]]
    assert result["project_context"]["outcome"] == "abstained"
    assert result["project_context"]["capsule"] is None
    evidence = result["project_search_evidence"]
    assert evidence["schema_version"] == "atc.project-search-evidence.v1"
    assert evidence["project_label"] == PROJECT
    assert evidence["evidence_kind"] == "partial_exact_name_content_matches"
    assert evidence["establishes_membership"] is False
    assert evidence["complete_task_coverage"] is False
    assert _evidence_ids(result) == {item["id"] for item in facts}
    assert {item["id"]: item for item in evidence["items"]} == {item["id"]: item for item in facts}
    assert evidence["used_chars"] + result["used_chars"] == result["total_used_chars"]
    assert result["total_used_chars"] <= 4000
    assert evidence["selection_metadata"]["used_chars"] == result["total_used_chars"]
    repeated = _bootstrap(client, reader, task_description=task)
    assert repeated["project_search_evidence"] == evidence
    assert client.get("/v1/admin/projects", headers=owner).json() == before


def test_arbitrary_mentions_are_search_evidence_but_other_project_bindings_are_excluded(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
) -> None:
    client, owner, _, reader = world
    mention = _save(client, owner, "An unrelated book mentions Silver Finch in its appendix.")
    bound = _save(
        client,
        owner,
        "Amber planning mentions Silver Finch as a comparison.",
        scopes=["project:amber"],
    )
    entity_bound = _save(
        client,
        owner,
        "Amber compares Silver Finch for its own room choice.",
        entity_key="project:amber",
        attribute_key="room",
    )
    identity = _save(
        client,
        owner,
        "Amber identity mentions Silver Finch as a comparison.",
        kind="project",
        structured_value={"project_name": "Amber"},
    )
    result = _bootstrap(client, reader)
    assert _evidence_ids(result) == {mention["id"]}
    assert result["items"] == []
    assert result["project_context"]["project_id"] is None
    serialized = json.dumps(result)
    for record in (bound, entity_bound, identity):
        assert record["id"] not in serialized
        assert record["content"] not in serialized


def test_provider_bound_fact_is_excluded_while_unbound_mention_survives_http_and_mcp(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, owner, reader_id, reader = world
    # A real public upload establishes a complete provider source, as in the
    # native binding probe. Publish the witnessed archive claims through a session.
    export = [
        {
            "id": "fixture-source",
            "mapping": {
                "u": {
                    "message": {
                        "id": "source-note",
                        "author": {"role": "user"},
                        "content": {"parts": ["Fact: The fixture reference is a paper catalog."]},
                    }
                }
            },
        }
    ]
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(export))
    uploaded = client.post(
        "/v1/admin/import",
        headers=owner,
        files={"file": ("fictional-provider.zip", bundle.getvalue(), "application/zip")},
        data={"provider": "chatgpt"},
    )
    assert uploaded.status_code == 200, uploaded.text
    source = uploaded.json()["source"]
    assert source["import_status"] == "complete"
    source_id = source["id"]
    witness = _post(
        client,
        owner,
        "/v1/admin/clients",
        {
            "name": "Fictional archive witness",
            "scopes": ["context:ingest", "context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        },
    )
    writer = {"Authorization": f"Bearer {witness['token']}"}
    begun = _post(
        client,
        writer,
        "/v1/ingestion/begin",
        {
            "mode": "archive_import",
            "accessible_sources": [source_id],
            "idempotency_key": "fictional-binding-archive",
        },
    )
    assert begun["status"] == "open"
    common = {
        "source_id": source_id,
        "source_type": "provider_archive",
        "source_service": "chatgpt",
        "explicit_user_statement": True,
        "confidence": 1.0,
        "scopes": ["project"],
    }
    claims = [
        {
            **common,
            "kind": "project",
            "content": "Amber",
            "structured_value": {"project_name": "Amber"},
            "source_reference": "conversations.json#conversation=other&message=anchor",
        },
        {
            **common,
            "kind": "fact",
            "content": "Amber reference manual compares Silver Finch fixtures.",
            "source_reference": "conversations.json#conversation=other&message=fact",
        },
        {
            **common,
            "kind": "fact",
            "explicit_user_statement": False,
            "content": "Amber catalog compares Silver Finch receiver models.",
            "source_reference": "conversations.json#conversation=other&message=reviewed-fact",
            "allowed_clients": [reader_id],
        },
    ]
    batch = _post(
        client,
        writer,
        "/v1/ingestion/batch",
        {
            "session_id": begun["session_id"],
            "idempotency_key": "fictional-binding-batch",
            "candidates": claims,
        },
    )
    assert len(batch["candidate_ids"]) == 3
    finished = _post(
        client,
        writer,
        "/v1/ingestion/finish",
        {
            "session_id": begun["session_id"],
            "coverage_report": {"available": [source_id], "complete": True},
        },
    )
    assert finished["status"] == "finished"
    assert finished["candidate_count"] == 3
    assert finished["accessible_sources"] == [source_id]
    assert finished["coverage"]["complete"] is True

    observations = client.get("/v1/admin/observations", headers=owner)
    assert observations.status_code == 200, observations.text
    by_id = {item["id"]: item for item in observations.json()["items"]}
    records = []
    for claim, observation_id in zip(claims, batch["candidate_ids"], strict=True):
        observation = by_id[observation_id]
        assert observation["session_id"] == begun["session_id"]
        assert observation["observation_origin"] == "archive_import"
        for key in ("source_id", "source_reference", "source_service", "source_type"):
            assert observation[key] == claim[key]
        expected_origin = "archive_import"
        if not claim["explicit_user_statement"]:
            assert observation["disposition"] == "tentative"
            assert observation["record_id"] is None
            approved = _post(
                client,
                owner,
                f"/v1/admin/candidates/{observation_id}/approve",
                {"reason": "Approve this fictional imported fact without changing its source."},
            )
            record_id = approved["id"]
            expected_origin = "local_admin"
        else:
            assert observation["disposition"] == "applied"
            record_id = observation["record_id"]
        truth_response = client.get(f"/v1/context/truth/{record_id}", headers=reader)
        assert truth_response.status_code == 200, truth_response.text
        truth = truth_response.json()
        record = truth["record"]
        assert truth["status"] == record["status"] == "current"
        assert record["deleted_at"] is None
        assert truth["conflict_state"] == "none"
        assert truth["source"]["id"] == source_id
        assert truth["source"]["import_status"] == "complete"
        assert record["observation_origin"] == expected_origin
        assert record["entity_key"] is None
        for key, value in claim.items():
            assert record[key] == value
        links = [link for link in truth["evidence"] if link["observation_id"] == observation_id]
        assert len(links) == 1
        assert links[0]["disposition"] == "applied"
        assert links[0]["observation_origin"] == expected_origin
        for key in ("source_id", "source_reference", "source_service", "source_type"):
            assert links[0][key] == claim[key]
        records.append(record)
    anchor, bound_fact, approved_import = records
    assert bound_fact["structured_value"] is None
    assert approved_import["structured_value"] is None
    assert approved_import["observation_origin"] == "local_admin"
    for fact in (bound_fact, approved_import):
        assert (
            anchor["source_reference"].split("&message=")[0]
            == (fact["source_reference"].split("&message=")[0])
        )
    projects = client.get("/v1/admin/projects", headers=owner)
    assert projects.status_code == 200, projects.text
    assert [item["name"] for item in projects.json()["items"]] == ["Amber"]

    mention = _save(client, owner, "Silver Finch visitors use the cedar entrance.")
    assert mention["observation_origin"] == "ongoing_client"
    assert all(mention[key] is None for key in ("source_id", "source_service", "source_type"))
    # All facts are current authorized search hits; the exclusion belongs to
    # fallback eligibility, not a weakened fixture or a capsule admission change.
    search = _post(client, reader, "/v1/context/search", {"query": PROJECT})
    assert {item["id"] for item in search["items"]} == {
        mention["id"],
        bound_fact["id"],
        approved_import["id"],
    }
    http_result = _bootstrap(client, reader)
    mcp_result = _mcp_first_read(client, reader_id, reader, monkeypatch)
    for result in (http_result, mcp_result):
        assert result["items"] == []
        assert result["project_context"]["outcome"] == "abstained"
        assert result["project_context"]["capsule"] is None
        assert result["project_search_evidence"]["items"] == [mention]
        serialized = json.dumps(result)
        for record in records:
            assert record["id"] not in serialized
            assert record["content"] not in serialized
        assert source_id not in serialized
        assert all(value not in serialized for value in batch["candidate_ids"])


@pytest.mark.parametrize(
    "signal",
    [
        None,
        "",
        "SF",
        " Silver Finch",
        "Silver  Finch",
        "Silver\nFinch",
        "Silver Finch or Amber",
        "Silver Finch, Amber",
        "project:Silver Finch",
        "C:/Silver Finch",
        "project-" + "a" * 64,
        "a" * 32,
        "a" * 161,
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "one two three four five six seven eight nine",
    ],
)
def test_fallback_requires_one_safe_explicit_named_signal(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]], signal: str | None
) -> None:
    client, owner, _, reader = world
    _save(client, owner, "Silver Finch visitors use the Elm Hall entrance.")
    result = _bootstrap(client, reader, current_project=signal, host_project_hint=PROJECT)
    assert "project_search_evidence" not in result


def test_name_matching_is_literal_case_insensitive_and_not_a_prefix_or_alias(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
) -> None:
    client, owner, _, reader = world
    exact = _save(client, owner, "silver finch counting uses a tally.")
    _save(client, owner, "Silver Finches use a different entrance.")
    _save(client, owner, "Silver Finch-extra equipment is unrelated.")
    _save(client, owner, "Finch Silver has a different room.")
    _save(client, owner, "SF is an abbreviation on an unrelated document.")
    assert _evidence_ids(_bootstrap(client, reader, current_project="SILVER FINCH")) == {
        exact["id"]
    }
    assert "project_search_evidence" not in _bootstrap(client, reader, current_project="Silver Fin")


@pytest.mark.parametrize("duplicate_names", [False, True])
def test_known_aliases_and_ambiguous_names_abstain_even_without_capsule_items(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]], duplicate_names: bool
) -> None:
    client, owner, _, reader = world
    for index in range(2 if duplicate_names else 1):
        _save(
            client,
            owner,
            f"Fictional identity {index}",
            kind="project",
            scopes=[f"project:identity-{index}"],
            # Separate declared slots keep equal display names from reinforcing
            # one canonical identity before ambiguity can reach retrieval.
            entity_key=f"project:identity-{index}",
            attribute_key="identity",
            structured_value={
                "project_name": PROJECT if duplicate_names else "Amber",
                "aliases": [] if duplicate_names else [PROJECT],
            },
        )
    projects = client.get("/v1/admin/projects", headers=owner)
    assert projects.status_code == 200, projects.text
    identities = projects.json()["items"]
    assert len(identities) == (2 if duplicate_names else 1)
    assert len({item["project_id"] for item in identities}) == len(identities)
    assert {item["name"] for item in identities} == {PROJECT if duplicate_names else "Amber"}
    assert all(item["item_count"] == 0 for item in identities)
    _save(client, owner, "Silver Finch is mentioned in unrelated prose.", kind="fact")
    result = _bootstrap(client, reader)
    assert result["project_context"]["capsule"] is None
    assert "project_search_evidence" not in result


def test_projection_failure_suppresses_fallback_without_blocking_ordinary_retrieval(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, owner, _, reader = world
    _save(client, owner, "Silver Finch visitors use Elm Hall.")

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise ProjectRuntimeError("fictional bounded projection failure")

    monkeypatch.setattr("allthecontext.core.app.build_project_runtime", unavailable)
    result = _bootstrap(client, reader)
    assert result["project_context"]["reason"] == "project_projection_unavailable"
    assert "project_search_evidence" not in result
    search = _post(client, reader, "/v1/context/search", {"query": PROJECT})
    assert len(search["items"]) == 1


def test_acl_scopes_confidence_conflicts_and_current_lifecycle_apply_before_selection(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
) -> None:
    client, owner, reader_id, reader = world
    outsider_id, outsider = _client(client, owner, "Unrelated reader")
    clients = client.get("/v1/admin/clients", headers=owner)
    assert clients.status_code == 200, clients.text
    owner_id = next(
        item["id"] for item in clients.json()["items"] if item["name"] == "Fictional owner"
    )
    # The correction's proposer must be permitted to access its target too.
    correction_clients = [owner_id, reader_id]
    good = _save(
        client,
        owner,
        "Silver Finch visitors use Elm Hall.",
        allowed_clients=[reader_id],
        confidence=0.8,
    )
    excluded = [
        _save(client, owner, "Silver Finch denied marker.", denied_clients=[reader_id]),
        _save(client, owner, "Silver Finch private marker.", allowed_clients=[outsider_id]),
        _save(client, owner, "Silver Finch low confidence marker.", confidence=0.79),
        _save(client, owner, "Silver Finch personal marker.", scopes=["personal"]),
        _save(client, owner, "Silver Finch future marker.", valid_from="2099-01-01T00:00:00Z"),
        _save(client, owner, "Silver Finch expired marker.", expires_at="2000-01-01T00:00:00Z"),
    ]
    old = _save(
        client,
        owner,
        "Silver Finch old equipment marker.",
        allowed_clients=correction_clients,
    )
    # Owner approval of a tentative proposal creates a separate successor;
    # an automatic targeted proposal would update the same canonical record.
    current = _save(
        client,
        owner,
        "Silver Finch uses loan receivers.",
        supersedes=old["id"],
        explicit_user_statement=False,
        allowed_clients=correction_clients,
    )
    assert current["id"] != old["id"]
    assert current["supersedes"] == old["id"]
    assert current["observation_origin"] == "local_admin"
    assert current["allowed_clients"] == old["allowed_clients"] == correction_clients
    for record in (old, current):
        assert all(record[key] is None for key in ("source_id", "source_service", "source_type"))
    current_response = client.get(f"/v1/context/truth/{current['id']}", headers=reader)
    assert current_response.status_code == 200, current_response.text
    current_truth = current_response.json()
    assert current_truth["record"] == current
    assert current_truth["status"] == current["status"] == "current"
    assert current_truth["conflict_state"] == "none"
    assert current_truth["source"] is None
    assert current_truth["superseded_by"] == []
    assert len(current_truth["evidence"]) == 1
    correction_link = current_truth["evidence"][0]
    assert correction_link["record_id"] == current["id"]
    assert correction_link["disposition"] == "applied"
    assert correction_link["observation_origin"] == "local_admin"
    assert all(
        correction_link[key] is None for key in ("source_id", "source_service", "source_type")
    )
    observations = client.get("/v1/admin/observations", headers=owner)
    assert observations.status_code == 200, observations.text
    correction = next(
        item
        for item in observations.json()["items"]
        if item["id"] == correction_link["observation_id"]
    )
    assert correction["record_id"] == current["id"]
    assert correction["supersedes"] == old["id"]
    assert correction["session_id"] is None
    assert correction["observation_origin"] == "local_admin"
    assert all(correction[key] is None for key in ("source_id", "source_service", "source_type"))
    old_response = client.get(f"/v1/admin/memory-truth/{old['id']}", headers=owner)
    assert old_response.status_code == 200, old_response.text
    old_truth = old_response.json()
    assert old_truth["status"] == old_truth["record"]["status"] == "superseded"
    assert old_truth["superseded_by"] == [current["id"]]
    assert old_truth["source"] is None
    assert all(
        link[key] is None
        for link in old_truth["evidence"]
        for key in ("source_id", "source_service", "source_type")
    )
    assert client.get(f"/v1/context/truth/{current['id']}", headers=outsider).status_code == 404
    outsider_result = json.dumps(_bootstrap(client, outsider))
    assert current["id"] not in outsider_result
    assert current["content"] not in outsider_result
    excluded.append(old)
    for room in ("copper", "indigo"):
        excluded.append(
            _save(
                client,
                owner,
                f"Silver Finch disputed room is {room}.",
                entity_key="fictional:finch",
                attribute_key="room",
                structured_value={"room": room},
                explicit_user_statement=False,
            )
        )
    truth = client.get(f"/v1/context/truth/{excluded[-1]['id']}", headers=reader)
    assert truth.status_code == 200, truth.text
    assert truth.json()["conflict_state"] == "active"
    result = _bootstrap(client, reader, requested_scopes=["project"])
    assert _evidence_ids(result) == {good["id"], current["id"]}
    returned_current = next(
        item for item in result["project_search_evidence"]["items"] if item["id"] == current["id"]
    )
    assert returned_current["supersedes"] == old["id"]
    serialized = json.dumps(result)
    for record in excluded:
        # An authorized predecessor ID remains legitimate correction lineage;
        # denied/out-of-scope record IDs must not appear in any envelope field.
        if record["id"] != old["id"]:
            assert record["id"] not in serialized
        assert record["content"] not in serialized
    assert "project_search_evidence" not in _bootstrap(client, reader, requested_scopes=["work"])


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_full_content_and_preferences_share_the_exact_character_boundary(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]], delta: int
) -> None:
    client, owner, _, reader = world
    fact = _save(client, owner, "Silver Finch " + "fictional equipment detail " * 9)
    preference = _save(client, owner, "Use concise answers.", kind="interaction_preference")
    cost = len(fact["content"]) + len(preference["content"]) + 128
    result = _bootstrap(client, reader, character_budget=cost + delta)
    assert [item["id"] for item in result["items"]] == [preference["id"]]
    assert result["total_used_chars"] <= cost + delta
    if delta < 0:
        assert "project_search_evidence" not in result
    else:
        assert result["project_search_evidence"]["items"] == [fact]
        assert result["total_used_chars"] == cost


def test_raw_capture_mentions_do_not_become_first_read_evidence(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
) -> None:
    client, owner, _, reader = world
    capture = _post(
        client,
        owner,
        "/v1/admin/clients",
        {"name": "Fictional capture", "scopes": ["context:capture"]},
    )
    capture_headers = {
        "Authorization": f"Bearer {capture['token']}",
        "X-ATC-Client-ID": capture["client"]["id"],
    }
    for sequence, role in enumerate(("user", "assistant", "imported"), start=1):
        captured = _post(
            client,
            capture_headers,
            "/v1/lifecycle/events",
            {
                "event_id": f"fictional-first-read-{sequence}",
                "idempotency_key": f"00000000-0000-4000-8000-{sequence:012d}",
                "session_id": "fictional-first-read",
                "conversation_id": "fictional-first-read",
                "sequence": sequence,
                "role": role,
                "content": "Silver Finch visitor count is kept in the raw transcript.",
                "observed_at": f"2026-11-17T12:00:{sequence:02d}Z",
            },
        )
        assert captured["status"] == "captured"
    assert "project_search_evidence" not in _bootstrap(client, reader)
    fact = _save(client, owner, "Silver Finch visitors use Elm Hall.")
    assert _evidence_ids(_bootstrap(client, reader)) == {fact["id"]}


def test_bounded_pool_and_total_item_limit_are_deterministic(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
) -> None:
    client, owner, _, reader = world
    preference = _save(client, owner, "Use concise answers.", kind="interaction_preference")
    for index in range(105):
        unique = " ".join(f"fiction{index}word{part}" for part in range(8))
        _save(client, owner, f"Silver Finch {unique}")
    first = _bootstrap(client, reader, character_budget=20000)
    second = _bootstrap(client, reader, character_budget=20000)
    assert [item["id"] for item in first["items"]] == [preference["id"]]
    evidence = first["project_search_evidence"]
    assert 0 < len(evidence["items"]) <= 31
    assert evidence["selection_metadata"]["candidate_pool_truncated"] is True
    assert "record_limit" in evidence["selection_metadata"]["truncation_reasons"]
    assert second["project_search_evidence"] == evidence


def test_ordinary_complete_pack_and_activated_capsule_do_not_add_fallback(
    world: tuple[TestClient, dict[str, str], str, dict[str, str]],
) -> None:
    client, owner, _, reader = world
    fact = _save(
        client,
        owner,
        "Juniper workflow uses a tally.",
        kind="fact",
        scopes=["project:juniper"],
    )
    result = _bootstrap(
        client, reader, task_description="Juniper workflow", current_project="juniper"
    )
    assert fact["id"] in {item["id"] for item in result["items"]}
    assert result["project_context"]["capsule"] is None
    assert "project_search_evidence" not in result
    _save(
        client,
        owner,
        PROJECT,
        kind="project",
        scopes=["project:finch"],
        structured_value={"project_name": PROJECT},
    )
    _save(client, owner, "Silver Finch uses Elm Hall.", scopes=["project:finch"])
    activated = _bootstrap(client, reader, character_budget=256)
    assert activated["project_context"]["outcome"] == "activated"
    assert "project_search_evidence" not in activated
    capsule_used = activated["project_context"]["capsule"]["used_chars"]
    assert activated["total_used_chars"] == activated["used_chars"] + capsule_used
    assert activated["total_used_chars"] <= 256


def test_corrected_and_forgotten_first_read_survives_core_and_mcp_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = CoreConfig.in_directory(tmp_path / "fictional-vault", require_auth=True)
    old_content = "Silver Finch visitors use the old Birch Room."
    current_content = "Silver Finch visitors use the revised Elm Hall."
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        owner, reader_id, reader = _setup(client)
        fact = _save(client, owner, old_content)
        correction = _post(
            client,
            owner,
            "/v1/ingestion/propose",
            {
                "kind": "correction",
                "content": current_content,
                "supersedes": fact["id"],
                "explicit_user_statement": True,
                "source_reference": "fictional:revised-room",
                "evidence": "The fictional owner revised the visitor room to Elm Hall.",
            },
        )
        assert correction["disposition"] == "applied"
        assert correction["record_id"] == fact["id"]

    for forgotten in (False, True):
        with (
            CoreService(config) as service,
            TestClient(create_app(config, service=service)) as client,
        ):
            result = _mcp_first_read(client, reader_id, reader, monkeypatch)
            assert result["items"] == []
            assert old_content not in json.dumps(result)
            if forgotten:
                assert "project_search_evidence" not in result
                assert current_content not in json.dumps(result)
            else:
                assert _evidence_ids(result) == {fact["id"]}
                item = result["project_search_evidence"]["items"][0]
                assert item["content"] == current_content
                assert item["source_reference"] == "fictional:revised-room"
                _post(
                    client,
                    owner,
                    "/v1/ingestion/forget",
                    {"record_id": fact["id"], "reason": "Forget the fictional room."},
                )
