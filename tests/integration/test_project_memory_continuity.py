from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from allthecontext import http_client, mcp_adapter
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.http_client import ContextHttpClient
from allthecontext.security import WITNESS_EXPLICIT_USER_STATEMENT
from fastapi.testclient import TestClient

INITIAL_DECISION = "Harborlight deployment region is cedar."
CORRECTED_DECISION = "Harborlight deployment region is maple."
OTHER_DECISION = "Lantern deployment region is willow."
INITIAL_REFERENCE = "fictional-notes:harborlight/region-v1"
CORRECTION_REFERENCE = "fictional-notes:harborlight/region-v2"


def _create_client(
    client: TestClient, owner: dict[str, str], name: str, scopes: list[str]
) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/v1/admin/clients", headers=owner, json={"name": name, "scopes": scopes}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["client"]["id"], {"Authorization": f"Bearer {payload['token']}"}


def _approve_fixture(
    client: TestClient,
    contributor: dict[str, str],
    owner: dict[str, str],
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    proposed = client.post("/v1/ingestion/propose", headers=contributor, json=payload)
    assert proposed.status_code == 200, proposed.text
    observation = proposed.json()
    # A propose grant alone cannot witness a user statement. Use real owner approval.
    assert observation["disposition"] == "tentative"
    assert observation["record_id"] is None
    approved = client.post(
        f"/v1/admin/candidates/{observation['id']}/approve",
        headers=owner,
        json={"reason": "Owner confirms this fictional project fixture."},
    )
    assert approved.status_code == 200, approved.text
    return observation["id"], approved.json()


def _retrieve_project(
    client: TestClient, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    search = client.post(
        "/v1/context/search",
        headers=headers,
        json={"query": "deployment region", "scopes": ["project:harborlight"]},
    )
    assert search.status_code == 200, search.text
    bootstrap = client.post(
        "/v1/context/bootstrap",
        headers=headers,
        json={
            "task_description": "deployment region",
            "current_project": "harborlight",
            "requested_scopes": ["project:harborlight"],
            "character_budget": 4_000,
        },
    )
    assert bootstrap.status_code == 200, bootstrap.text
    return search.json(), bootstrap.json()


def _assert_forgotten(
    client: TestClient,
    reader: dict[str, str],
    owner: dict[str, str],
    record_id: str,
    other_id: str,
) -> dict[str, Any]:
    for route in (f"/v1/context/{record_id}", f"/v1/context/truth/{record_id}"):
        assert client.get(route, headers=reader).status_code == 404
    search, bootstrap = _retrieve_project(client, reader)
    assert search["items"] == []
    assert bootstrap["items"] == []
    for content in (INITIAL_DECISION, CORRECTED_DECISION, OTHER_DECISION):
        assert content not in json.dumps(bootstrap)

    # A scoped forget leaves the independently approved decision retrievable.
    other = client.get(f"/v1/context/{other_id}", headers=reader)
    assert other.status_code == 200, other.text
    assert other.json()["content"] == OTHER_DECISION
    assert client.get(f"/v1/admin/memory-truth/{record_id}", headers=reader).status_code == 403
    history = client.get(f"/v1/admin/memory-truth/{record_id}", headers=owner)
    assert history.status_code == 200, history.text
    truth = history.json()
    assert truth["status"] == "deleted"
    assert truth["record"]["id"] == record_id
    assert truth["record"]["deleted_at"] is not None
    assert truth["record"]["content"] == CORRECTED_DECISION
    return truth


def _mcp_project(
    client: TestClient,
    reader_id: str,
    reader: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    # Retain the SDK tool and real HTTP-client decoding; only transport is local.
    def request(method: str, url: str, **kwargs: Any) -> Any:
        kwargs.pop("timeout", None)
        kwargs.pop("trust_env", None)
        return client.request(method, url, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(http_client.httpx, "request", request)
        patch.setattr(
            mcp_adapter,
            "_client",
            lambda: ContextHttpClient(
                "http://testserver", reader_id, reader["Authorization"].removeprefix("Bearer ")
            ),
        )
        call = anyio.run(
            mcp_adapter.build_mcp().call_tool,
            "bootstrap_context",
            {
                "task_description": "deployment region",
                "current_project": "harborlight",
                "requested_scopes": ["project:harborlight"],
                "character_budget": 4_000,
            },
        )
    assert call.structured_content is not None
    return call.structured_content


@pytest.mark.parametrize("observation_acl", ["allow", "deny"])
def test_project_decision_correction_and_forget_survive_core_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observation_acl: str
) -> None:
    """Public HTTP state survives two Core/client recreations over one disposable vault."""
    config = CoreConfig.in_directory(tmp_path / "fictional-vault", require_auth=True)
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        setup = client.post("/v1/setup", json={"name": "Fictional owner", "scopes": []})
        assert setup.status_code == 200, setup.text
        owner = {"Authorization": f"Bearer {setup.json()['token']}"}
        _, contributor = _create_client(
            client, owner, "Unwitnessed contributor", ["context:propose"]
        )
        reader_id, reader = _create_client(client, owner, "Project reader", ["context:read"])
        partial_id, partial = _create_client(client, owner, "Partial reader", ["context:read"])
        writer_id, writer = _create_client(
            client, owner, "Project writer", ["context:propose", WITNESS_EXPLICIT_USER_STATEMENT]
        )
        outsider_id, outsider = _create_client(
            client,
            owner,
            "Outside project",
            ["context:read", "context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        )
        common = {
            "allowed_clients": [reader_id, partial_id, writer_id],
            "source_service": "fictional-project-notes",
            "source_type": "direct_user_statement",
            "explicit_user_statement": True,
        }
        _approve_fixture(
            client,
            contributor,
            owner,
            {
                **common,
                "kind": "project",
                "content": "Harborlight",
                "scopes": ["project:harborlight"],
                "structured_value": {"project_name": "Harborlight"},
                "source_reference": "fictional-notes:harborlight/identity",
            },
        )
        original_id, original = _approve_fixture(
            client,
            contributor,
            owner,
            {
                **common,
                "kind": "project_decision",
                "content": INITIAL_DECISION,
                "scopes": ["project:harborlight"],
                "entity_key": "project:harborlight",
                "attribute_key": "deployment_region",
                "source_reference": INITIAL_REFERENCE,
            },
        )
        _, other = _approve_fixture(
            client,
            contributor,
            owner,
            {
                **common,
                "kind": "project_decision",
                "content": OTHER_DECISION,
                "scopes": ["project:lantern"],
                "entity_key": "project:lantern",
                "attribute_key": "deployment_region",
                "source_reference": "fictional-notes:lantern/region-v1",
            },
        )
        record_id = original["id"]
        search, bootstrap = _retrieve_project(client, reader)
        assert [item["content"] for item in search["items"]] == [INITIAL_DECISION]
        decisions = bootstrap["project_context"]["capsule"]["sections"]["decisions"]
        assert [item["text"] for item in decisions] == [INITIAL_DECISION]
        project_id = bootstrap["project_context"]["project_id"]

        correction_payload = {
            "kind": "correction",
            "content": CORRECTED_DECISION,
            "supersedes": record_id,
            "explicit_user_statement": True,
            "source_service": common["source_service"],
            "source_type": common["source_type"],
            "source_reference": CORRECTION_REFERENCE,
            "evidence": "The owner confirmed the revised fictional deployment region.",
            "idempotency_key": "harborlight-region-correction",
        }
        corrected = client.post("/v1/ingestion/propose", headers=writer, json=correction_payload)
        assert corrected.status_code == 200, corrected.text
        correction = corrected.json()
        assert correction["disposition"] == "applied"
        assert correction["record_id"] == record_id
        assert correction["id"] != original_id
        assert correction["explicit_user_statement"] is True

        # Owned fictional fixture: observation ACLs may be narrower than current
        # canonical access. There is no public observation-ACL editing route.
        with service.store.transaction() as connection:
            connection.execute(
                "UPDATE context_candidates SET allowed_clients_json=?,denied_clients_json=? "
                "WHERE id=?",
                (
                    json.dumps([reader_id, writer_id]) if observation_acl == "allow" else "[]",
                    json.dumps([partial_id]) if observation_acl == "deny" else "[]",
                    original_id,
                ),
            )

    # No post-correction retrieval happens until both Core and HTTP client are recreated.
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        assert client.get(f"/v1/context/{record_id}", headers=writer).status_code == 403
        assert client.post(
            "/v1/ingestion/propose", headers=reader, json=correction_payload
        ).status_code == 403
        forget_payload = {"record_id": record_id, "reason": "Forget the fictional region decision."}
        assert client.post(
            "/v1/ingestion/forget", headers=reader, json=forget_payload
        ).status_code == 403
        for route in (f"/v1/context/{record_id}", f"/v1/context/truth/{record_id}"):
            assert client.get(route, headers=outsider).status_code == 404
        denied = client.post("/v1/ingestion/propose", headers=outsider, json=correction_payload)
        assert denied.status_code == 200, denied.text
        assert denied.json()["disposition"] == "ignored"
        assert client.post(
            "/v1/ingestion/forget", headers=outsider, json=forget_payload
        ).status_code == 404
        denied_search, denied_bootstrap = _retrieve_project(client, outsider)
        assert denied_search["items"] == []
        assert denied_bootstrap["items"] == []
        assert CORRECTED_DECISION not in json.dumps(denied_bootstrap)

        current = client.get(f"/v1/context/{record_id}", headers=reader)
        assert current.status_code == 200, current.text
        assert current.json()["content"] == CORRECTED_DECISION
        assert current.json()["version"] == original["version"] + 1
        assert current.json()["kind"] == "project_decision"
        assert current.json()["scopes"] == ["project:harborlight"]
        assert current.json()["source_reference"] == CORRECTION_REFERENCE
        truth_response = client.get(f"/v1/context/truth/{record_id}", headers=reader)
        assert truth_response.status_code == 200, truth_response.text
        truth = truth_response.json()
        assert truth["status"] == "current"
        assert truth["record"] == current.json()
        evidence = {item["observation_id"]: item for item in truth["evidence"]}
        assert set(evidence) == {original_id, correction["id"]}
        for observation_id, content, reference, relationship in (
            (original_id, INITIAL_DECISION, INITIAL_REFERENCE, "applied"),
            (correction["id"], CORRECTED_DECISION, CORRECTION_REFERENCE, "updated"),
        ):
            item = evidence[observation_id]
            assert item["record_id"] == record_id
            assert item["content"] == content
            assert item["source_reference"] == reference
            assert item["source_service"] == common["source_service"]
            assert item["source_type"] == common["source_type"]
            assert item["disposition"] == "applied"
            assert item["relationship"] == relationship

        search, bootstrap = _retrieve_project(client, reader)
        assert [(item["id"], item["content"]) for item in search["items"]] == [
            (record_id, CORRECTED_DECISION)
        ]
        assert [(item["id"], item["content"]) for item in bootstrap["items"]] == [
            (record_id, CORRECTED_DECISION)
        ]
        project = bootstrap["project_context"]
        assert project["reason"] == "explicit_project_match"
        assert project["project_id"] == project_id
        decisions = project["capsule"]["sections"]["decisions"]
        assert [(item["record_id"], item["text"]) for item in decisions] == [
            (record_id, CORRECTED_DECISION)
        ]
        assert set(decisions[0]["provenance_ids"]) == {record_id, *evidence}
        assert set(project["capsule"]["provenance_ids"]) == {record_id, *evidence}
        assert INITIAL_DECISION not in json.dumps((search, bootstrap))
        assert OTHER_DECISION not in json.dumps((search, bootstrap))
        stale_search = client.post(
            "/v1/context/search",
            headers=reader,
            json={"query": "cedar", "scopes": ["project:harborlight"]},
        )
        assert stale_search.status_code == 200, stale_search.text
        assert stale_search.json()["items"] == []
        other_search = client.post(
            "/v1/context/search",
            headers=reader,
            json={"query": "deployment region", "scopes": ["project:lantern"]},
        )
        assert other_search.status_code == 200, other_search.text
        assert [item["id"] for item in other_search.json()["items"]] == [other["id"]]

        # Reader truth is the independent public oracle for linked evidence.
        # Interleave the same Core's readers, then use fresh SDK/HTTP clients.
        views = {reader_id: project}
        for client_id, headers, expected_evidence in (
            (partial_id, partial, {correction["id"]}),
            (reader_id, reader, {original_id, correction["id"]}),
            (outsider_id, outsider, set()),
            (partial_id, partial, {correction["id"]}),
            (reader_id, reader, {original_id, correction["id"]}),
        ):
            oracle = client.get(f"/v1/context/truth/{record_id}", headers=headers)
            scoped_search, scoped_bootstrap = _retrieve_project(client, headers)
            scoped_project = scoped_bootstrap["project_context"]
            if client_id == outsider_id:
                assert oracle.status_code == 404
                assert scoped_project["outcome"] == "abstained"
                assert scoped_project["capsule"] is None
                assert scoped_search["items"] == []
                assert scoped_bootstrap["items"] == []
                for hidden_id in (record_id, original_id, correction["id"]):
                    assert hidden_id not in json.dumps(scoped_bootstrap)
            else:
                assert oracle.status_code == 200, oracle.text
                scoped_truth = oracle.json()
                assert scoped_truth["record"] == current.json()
                visible = {item["observation_id"] for item in scoped_truth["evidence"]}
                assert visible == expected_evidence
                capsule = scoped_project["capsule"]
                assert scoped_project["project_id"] == project_id
                assert scoped_project["reason"] == "explicit_project_match"
                assert capsule["project_name"] == "Harborlight"
                assert capsule["dependency_ids"] == [record_id]
                assert capsule["omitted_count"] == 0
                assert capsule["used_chars"] == len(CORRECTED_DECISION)
                assert set(capsule["provenance_ids"]) == {record_id, *visible}
                assert capsule["sections"]["decisions"] == [
                    {**decisions[0], "provenance_ids": sorted({record_id, *visible})}
                ]
                assert [(item["id"], item["content"]) for item in scoped_search["items"]] == [
                    (record_id, CORRECTED_DECISION)
                ]
                assert scoped_truth["record"]["source_reference"] == CORRECTION_REFERENCE
                if client_id == partial_id:
                    for hidden in (original_id, INITIAL_REFERENCE, INITIAL_DECISION):
                        assert hidden not in json.dumps((scoped_truth, scoped_bootstrap))
            if client_id in views:
                assert scoped_project == views[client_id]
            views[client_id] = scoped_project
            assert _mcp_project(client, client_id, headers, monkeypatch)["project_context"] == (
                scoped_project
            )

        assert views[partial_id]["snapshot_revision"] != project["snapshot_revision"]
        admin_capsule = client.get(
            f"/v1/admin/projects/{project_id}/capsule?character_budget=2000&item_budget=32",
            headers=owner,
        )
        assert admin_capsule.status_code == 200, admin_capsule.text
        assert admin_capsule.json() == project["capsule"]

        # Exercise the existing evidence bound with this small ordinary fixture:
        # the inaccessible earlier observation cannot displace the visible update.
        with monkeypatch.context() as patch:
            patch.setattr("allthecontext.storage.MAX_TRUTH_EVIDENCE", 1)
            bounded_truth = client.get(f"/v1/context/truth/{record_id}", headers=partial)
            assert bounded_truth.status_code == 200, bounded_truth.text
            assert [item["observation_id"] for item in bounded_truth.json()["evidence"]] == [
                correction["id"]
            ]
            assert _retrieve_project(client, partial)[1]["project_context"] == views[partial_id]

        forgotten = client.post("/v1/ingestion/forget", headers=writer, json=forget_payload)
        assert forgotten.status_code == 200, forgotten.text
        assert forgotten.json()["disposition"] == "applied"
        assert forgotten.json()["record_id"] == record_id
        deleted_truth = _assert_forgotten(client, reader, owner, record_id, other["id"])
        assert deleted_truth["record"]["version"] == current.json()["version"] + 1
        assert deleted_truth["evidence"] == truth["evidence"]
        assert deleted_truth["history_count"] == truth["history_count"] + 1
        assert _assert_forgotten(client, partial, owner, record_id, other["id"]) == deleted_truth

    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        # Reversible deletion preserves authorized history; it does not promise a purge.
        assert _assert_forgotten(client, reader, owner, record_id, other["id"]) == deleted_truth
        assert _assert_forgotten(client, partial, owner, record_id, other["id"]) == deleted_truth
        for client_id, headers in ((partial_id, partial), (reader_id, reader)):
            result = _mcp_project(client, client_id, headers, monkeypatch)
            for forgotten_id in (record_id, original_id, correction["id"]):
                assert forgotten_id not in json.dumps(result)
