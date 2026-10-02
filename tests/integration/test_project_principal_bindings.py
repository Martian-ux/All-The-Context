from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from allthecontext import claude_code_hook, codex_hook
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.export import NON_PORTABLE_SECURITY_TABLES, create_export, restore_export
from allthecontext.hook_context import REFERENCE_FRAME
from allthecontext.http_client import ContextApiError
from allthecontext.models import BootstrapRequest
from allthecontext.project_binding import ProjectBindingError, ProjectBindings
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore
from fastapi.testclient import TestClient


@dataclass
class Fixture:
    core: CoreService
    client: TestClient
    owner: dict[str, str]
    anchors: dict[str, str]
    projects: dict[str, str]

    def principal(
        self, project: str | None, scopes: list[str]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        created = self.client.post(
            "/v1/admin/clients",
            headers=self.owner,
            json={"name": "Synthetic binding principal", "scopes": scopes},
        )
        assert created.status_code == 200, created.text
        value = created.json()
        headers = {"Authorization": "Bearer " + value["token"]}
        receipt: dict[str, Any] = {"client_id": value["client"]["id"]}
        if project is not None:
            bound = self.client.post(
                "/v1/admin/project-bindings",
                headers=self.owner,
                json={
                    "client_id": receipt["client_id"],
                    "canonical_project_id": self.projects[project],
                },
            )
            assert bound.status_code == 200, bound.text
            receipt.update(bound.json())
        return headers, receipt

    def propose(self, kind: str, content: str, scopes: list[str], **extra: Any) -> str:
        response = self.client.post(
            "/v1/ingestion/propose",
            headers=self.owner,
            json={
                "kind": kind,
                "content": content,
                "scopes": scopes,
                "explicit_user_statement": True,
                **extra,
            },
        )
        assert response.status_code == 200, response.text
        record_id = response.json()["record_id"]
        assert isinstance(record_id, str), response.text
        return record_id

    def bootstrap(self, headers: dict[str, str], query: str = "", **extra: Any) -> dict[str, Any]:
        response = self.client.post(
            "/v1/context/bootstrap",
            headers=headers,
            json={"query": query, "budget_chars": 8000, **extra},
        )
        assert response.status_code == 200, response.text
        return dict(response.json())


@pytest.fixture
def fixture(tmp_path: Path) -> Iterator[Fixture]:
    config = CoreConfig.in_directory(tmp_path / "vault", require_auth=True)
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        setup = client.post("/v1/setup", json={"name": "Synthetic owner"})
        assert setup.status_code == 200, setup.text
        value = Fixture(core, client, {"Authorization": "Bearer " + setup.json()["token"]}, {}, {})
        for name in ("borealis", "cedar"):
            # Intentionally identical labels: names cannot choose identity.
            value.anchors[name] = value.propose(
                "project",
                name + " project",
                ["project:" + name],
                structured_value={"project_name": "Same display label"},
                entity_key=name,
                attribute_key="identity",
            )
            registered = client.post(
                "/v1/admin/project-identities",
                headers=value.owner,
                json={"anchor_record_id": value.anchors[name]},
            )
            assert registered.status_code == 200, registered.text
            value.projects[name] = registered.json()["canonical_project_id"]
            value.propose("goal", name + " storage uses SQLite", ["project:" + name])
        assert value.anchors["borealis"] != value.anchors["cedar"]
        yield value


def event(content: str = "I prefer concise answers.") -> dict[str, Any]:
    return {
        "event_id": "evt-" + uuid.uuid4().hex,
        "idempotency_key": str(uuid.uuid4()),
        "session_id": "synthetic-session",
        "conversation_id": "synthetic-conversation",
        "sequence": 1,
        "role": "user",
        "content": content,
    }


def test_canonical_keys_and_immutable_receipts_do_not_use_labels(fixture: Fixture) -> None:
    assert fixture.projects["borealis"] != fixture.projects["cedar"]
    reader, receipt = fixture.principal("borealis", ["context:read"])
    response = fixture.client.get("/v1/context/project-binding", headers=reader)
    assert response.status_code == 200, response.text
    assert response.json() == {k: v for k, v in receipt.items() if k != "client_id"}
    same = fixture.client.post(
        "/v1/admin/project-bindings",
        headers=fixture.owner,
        json={
            "client_id": receipt["client_id"],
            "canonical_project_id": fixture.projects["borealis"],
        },
    )
    assert same.status_code == 200
    assert same.json() == {k: v for k, v in receipt.items() if k != "client_id"}
    changed = fixture.client.post(
        "/v1/admin/project-bindings",
        headers=fixture.owner,
        json={"client_id": receipt["client_id"], "canonical_project_id": fixture.projects["cedar"]},
    )
    assert changed.status_code == 409
    again = fixture.client.post(
        "/v1/admin/project-identities",
        headers=fixture.owner,
        json={"anchor_record_id": fixture.anchors["borealis"]},
    )
    assert again.json()["canonical_project_id"] == fixture.projects["borealis"]
    fixture.principal("borealis", ["context:capture"])
    assert (
        fixture.bootstrap(reader)["project_binding"]["canonical_project_id"]
        == (fixture.projects["borealis"])
    )


@pytest.mark.parametrize("query", ["", "storage", "cedar storage"])
def test_exact_filter_applies_to_records_preferences_capsule_and_get(
    fixture: Fixture,
    query: str,
) -> None:
    for name in ("borealis", "cedar"):
        fixture.propose(
            "interaction_preference",
            name + " preference",
            ["project:" + name],
            entity_key=name,
            attribute_key="response_style",
        )
    fixture.propose(
        "interaction_preference",
        "Legacy mixed cedar preference",
        [],
        entity_key="legacy",
        attribute_key="response_style",
    )
    ambiguous = fixture.propose("goal", "Ambiguous storage", ["project:borealis", "project:cedar"])
    reader, _ = fixture.principal("borealis", ["context:read"])
    result = fixture.bootstrap(reader, query, current_project="cedar", host_project_hint="cedar")
    assert result["project_context"]["reason"] == "registered_project_binding"
    assert "cedar" not in json.dumps(result)
    assert all(item["scopes"] == ["project:borealis"] for item in result["items"])
    assert ambiguous not in {item["id"] for item in result["items"]}
    search = fixture.client.post("/v1/context/search", headers=reader, json={"query": ""})
    assert search.status_code == 200, search.text
    assert "cedar" not in search.text
    for record_id in (fixture.anchors["cedar"], ambiguous):
        for route in ("/v1/context/", "/v1/context/truth/"):
            response = fixture.client.get(route + record_id, headers=reader)
            assert response.status_code == 404


def test_capture_partitions_existing_slots_and_persists_replay_context(fixture: Fixture) -> None:
    capture_a, receipt_a = fixture.principal("borealis", ["context:capture"])
    capture_b, _ = fixture.principal("cedar", ["context:capture"])
    reader_a, _ = fixture.principal("borealis", ["context:read"])
    reader_b, _ = fixture.principal("cedar", ["context:read"])
    payload = event()
    first = fixture.client.post("/v1/lifecycle/events", headers=capture_a, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["project_binding"]["binding_id"] == receipt_a["binding_id"]
    other = fixture.client.post(
        "/v1/lifecycle/events", headers=capture_b, json=event("I prefer detailed explanations.")
    )
    assert other.status_code == 200, other.text
    replay = fixture.client.post("/v1/lifecycle/events", headers=capture_a, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "replayed"
    assert replay.json()["observation_id"] == first.json()["observation_id"]
    a = fixture.bootstrap(reader_a)
    b = fixture.bootstrap(reader_b)
    assert any("concise" in item["content"] for item in a["items"])
    assert all("detailed" not in item["content"] for item in a["items"])
    assert any("detailed" in item["content"] for item in b["items"])
    assert all("concise" not in item["content"] for item in b["items"])
    with fixture.core.store.connect() as connection:
        row = connection.execute(
            "SELECT normalized_payload_json FROM capture_events WHERE id=?",
            (first.json()["capture_event_id"],),
        ).fetchone()
        assert row is not None
        assert json.loads(row[0])["project_binding"] == first.json()["project_binding"]


@pytest.mark.parametrize(
    "failure", ["revoked_binding", "missing_binding", "deleted_anchor", "revoked_client"]
)
def test_bound_reader_fails_closed_without_legacy_fallback(fixture: Fixture, failure: str) -> None:
    reader, receipt = fixture.principal("borealis", ["context:read"])
    if failure == "revoked_binding":
        ProjectBindings(fixture.core.store).revoke(receipt["binding_id"])
    elif failure == "missing_binding":
        with fixture.core.store.transaction() as connection:
            connection.execute(
                "DELETE FROM principal_project_bindings WHERE id=?", (receipt["binding_id"],)
            )
    elif failure == "deleted_anchor":
        fixture.core.store.delete_record(fixture.anchors["borealis"], reason="Synthetic deletion")
    else:
        fixture.core.store.revoke_client(receipt["client_id"])
    for route, body in (
        ("/v1/context/bootstrap", {"query": "cedar storage"}),
        ("/v1/context/search", {"query": ""}),
    ):
        response = fixture.client.post(route, headers=reader, json=body)
        assert response.status_code in {401, 403}
        assert "Same display label" not in response.text
        assert "cedar" not in response.text
    with pytest.raises(ProjectBindingError):
        fixture.core.retrieval.bootstrap(
            BootstrapRequest(),
            ClientPrincipal(receipt["client_id"], "Forged", frozenset({"*"})),
        )


def test_caller_authority_fields_are_rejected_and_legacy_is_explicit(fixture: Fixture) -> None:
    reader, _ = fixture.principal(None, ["context:read"])
    capture, _ = fixture.principal(None, ["context:capture"])
    assert fixture.bootstrap(reader)["project_binding"] == {"state": "legacy_unbound"}
    for field in ("project_id", "canonical_project_id", "project_binding", "cwd", "authorized"):
        response = fixture.client.post(
            "/v1/lifecycle/events",
            headers=capture,
            json={**event(), field: fixture.projects["cedar"]},
        )
        assert response.status_code == 422
    unauthorized = fixture.client.post(
        "/v1/admin/project-identities",
        headers=reader,
        json={"anchor_record_id": fixture.anchors["borealis"]},
    )
    assert unauthorized.status_code == 403
    first = fixture.client.post("/v1/lifecycle/events", headers=capture, json=event())
    assert first.status_code == 200
    assert "project_binding" not in first.json()


def test_immutability_is_enforced_by_database(fixture: Fixture) -> None:
    _, receipt = fixture.principal("borealis", ["context:read"])
    with pytest.raises(sqlite3.IntegrityError), fixture.core.store.transaction() as connection:
        connection.execute(
            "UPDATE principal_project_bindings SET canonical_project_id=? WHERE id=?",
            (fixture.projects["cedar"], receipt["binding_id"]),
        )
    with pytest.raises(sqlite3.IntegrityError), fixture.core.store.transaction() as connection:
        connection.execute(
            "UPDATE client_registrations SET project_binding_required=0 WHERE id=?",
            (receipt["client_id"],),
        )


def test_bound_partial_records_preserve_unbound_completeness_and_capsule(
    fixture: Fixture,
) -> None:
    reader, _ = fixture.principal("borealis", ["context:read"])
    result = fixture.bootstrap(reader, "borealis storage rollback")
    assert result["items"]
    assert all(item["scopes"] == ["project:borealis"] for item in result["items"])
    assert any(item["content"] == "borealis storage uses SQLite" for item in result["items"])
    assert result["project_context"]["outcome"] == "activated"
    assert "SQLite" in json.dumps(result["project_context"])
    unbound, _ = fixture.principal(None, ["context:read"])
    assert fixture.bootstrap(unbound, "borealis storage rollback")["items"] == []
    # Empty record selection still does not erase separately activated capsule context.
    disjoint = fixture.bootstrap(
        reader, "borealis storage rollback", requested_scopes=["project:cedar"]
    )
    assert disjoint["items"] == []
    assert disjoint["project_context"]["outcome"] == "activated"
    assert "SQLite" in json.dumps(disjoint["project_context"])


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_actual_hook_transport_uses_principal_binding_not_task_hints(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
    app: str,
) -> None:
    capture, _ = fixture.principal("borealis", ["context:capture"])
    reader, receipt = fixture.principal("borealis", ["context:read"])
    result = fixture.client.post("/v1/lifecycle/events", headers=capture, json=event())
    assert result.status_code == 200

    class Client:
        def bootstrap_context_core_only(self, payload: dict[str, Any]) -> dict[str, Any]:
            response = fixture.client.post("/v1/context/bootstrap", headers=reader, json=payload)
            if response.status_code != 200:
                raise ContextApiError(response.status_code, "refused", "synthetic-private")
            return dict(response.json())

    module = codex_hook if app == "codex" else claude_code_hook
    monkeypatch.setattr(module, "_hook_client", lambda: Client())
    text = (
        codex_hook._retrieve_hook_context("cedar storage", 8000)
        if app == "codex"
        else claude_code_hook._retrieve_hook_context("cedar storage")
    )
    parts = [json.loads(part) for part in text.removeprefix(REFERENCE_FRAME).split("\n\n")]
    assert any("concise" in part.get("content", "") for part in parts)
    assert all("cedar" not in part.get("content", "") for part in parts)
    ProjectBindings(fixture.core.store).revoke(receipt["binding_id"])
    unavailable = (
        codex_hook._retrieve_hook_context("cedar storage", 8000)
        if app == "codex"
        else claude_code_hook._retrieve_hook_context("cedar storage")
    )
    assert "retrieval_unavailable" in unavailable
    assert "concise" not in unavailable
    assert "synthetic-private" not in unavailable


def test_capture_receipt_uses_transaction_context_when_binding_changes_after_preflight(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture, receipt = fixture.principal(None, ["context:capture"])
    original = fixture.core.store.record_client_capture
    binding: dict[str, Any] = {}

    def bind_then_record(**kwargs: Any) -> Any:
        binding.update(
            ProjectBindings(fixture.core.store).register_client(
                receipt["client_id"], fixture.projects["borealis"]
            )
        )
        return original(**kwargs)

    monkeypatch.setattr(fixture.core.store, "record_client_capture", bind_then_record)
    response = fixture.client.post("/v1/lifecycle/events", headers=capture, json=event())
    assert response.status_code == 200, response.text
    assert response.json()["project_binding"] == binding
    reader, _ = fixture.principal("borealis", ["context:read"])
    assert any("concise" in item["content"] for item in fixture.bootstrap(reader)["items"])


def test_capture_rechecks_revocation_inside_transaction(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture, receipt = fixture.principal("borealis", ["context:capture"])
    original = fixture.core.store.record_client_capture

    def revoke_then_record(**kwargs: Any) -> Any:
        ProjectBindings(fixture.core.store).revoke(receipt["binding_id"])
        return original(**kwargs)

    monkeypatch.setattr(fixture.core.store, "record_client_capture", revoke_then_record)
    response = fixture.client.post("/v1/lifecycle/events", headers=capture, json=event())
    assert response.status_code == 403, response.text
    with fixture.core.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["missing_binding", "deleted_anchor", "revoked_client"])
def test_capture_rejects_invalid_binding_without_staging(fixture: Fixture, failure: str) -> None:
    capture, receipt = fixture.principal("borealis", ["context:capture"])
    if failure == "missing_binding":
        with fixture.core.store.transaction() as connection:
            connection.execute(
                "DELETE FROM principal_project_bindings WHERE id=?", (receipt["binding_id"],)
            )
    elif failure == "deleted_anchor":
        fixture.core.store.delete_record(fixture.anchors["borealis"], reason="Synthetic deletion")
    else:
        fixture.core.store.revoke_client(receipt["client_id"])
    response = fixture.client.post("/v1/lifecycle/events", headers=capture, json=event())
    assert response.status_code in {401, 403}, response.text
    with fixture.core.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 0


@pytest.mark.parametrize("scopes", [["*"], ["admin"], ["context:read", "context:capture"]])
def test_binding_refuses_overpowered_or_mixed_principals(
    fixture: Fixture, scopes: list[str]
) -> None:
    _, receipt = fixture.principal(None, scopes)
    response = fixture.client.post(
        "/v1/admin/project-bindings",
        headers=fixture.owner,
        json={
            "client_id": receipt["client_id"],
            "canonical_project_id": fixture.projects["borealis"],
        },
    )
    assert response.status_code == 403, response.text


def test_legacy_capture_cannot_be_reinterpreted_by_later_binding(fixture: Fixture) -> None:
    capture, receipt = fixture.principal(None, ["context:capture"])
    first = fixture.client.post("/v1/lifecycle/events", headers=capture, json=event())
    assert first.status_code == 200, first.text
    response = fixture.client.post(
        "/v1/admin/project-bindings",
        headers=fixture.owner,
        json={
            "client_id": receipt["client_id"],
            "canonical_project_id": fixture.projects["borealis"],
        },
    )
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("failure", ["expired", "future", "scope_changed", "denied_anchor"])
def test_stale_or_unauthorized_anchor_fails_closed(fixture: Fixture, failure: str) -> None:
    reader, receipt = fixture.principal("borealis", ["context:read"])
    column, value = {
        "expired": ("expires_at", "2000-01-01T00:00:00Z"),
        "future": ("valid_from", "2099-01-01T00:00:00Z"),
        "scope_changed": ("scopes_json", '["project:cedar"]'),
        "denied_anchor": ("denied_clients_json", json.dumps([receipt["client_id"]])),
    }[failure]
    with fixture.core.store.transaction() as connection:
        connection.execute(
            f"UPDATE context_records SET {column}=? WHERE id=?",
            (value, fixture.anchors["borealis"]),
        )
    response = fixture.client.post(
        "/v1/context/bootstrap", headers=reader, json={"query": "storage"}
    )
    assert response.status_code == 403, response.text
    assert "SQLite" not in response.text


@pytest.mark.parametrize(
    ("kind", "scopes", "explicit"),
    [
        ("goal", ["project:new"], True),
        ("project", ["project:a", "project:b"], True),
        ("project", ["project:new"], False),
    ],
)
def test_project_registration_requires_explicit_unambiguous_project_anchor(
    fixture: Fixture, kind: str, scopes: list[str], explicit: bool
) -> None:
    anchor = fixture.propose(kind, "Synthetic registration target", scopes)
    if not explicit:
        with fixture.core.store.transaction() as connection:
            connection.execute(
                "UPDATE context_records SET explicit_user_statement=0 WHERE id=?", (anchor,)
            )
    response = fixture.client.post(
        "/v1/admin/project-identities", headers=fixture.owner, json={"anchor_record_id": anchor}
    )
    assert response.status_code == 403, response.text


def test_foreign_vault_principal_and_identity_are_not_legacy_authority(
    fixture: Fixture, tmp_path: Path
) -> None:
    reader, receipt = fixture.principal("borealis", ["context:read"])
    foreign_config = CoreConfig.in_directory(tmp_path / "foreign", require_auth=True)
    with (
        CoreService(foreign_config) as foreign,
        TestClient(create_app(foreign_config, service=foreign)) as client,
    ):
        owner = client.post("/v1/setup", json={"name": "Foreign synthetic owner"}).json()
        response = client.post("/v1/context/bootstrap", headers=reader, json={"query": "storage"})
        assert response.status_code == 401
        with pytest.raises(ProjectBindingError):
            ProjectBindings(foreign.store).resolve(
                ClientPrincipal(receipt["client_id"], "", frozenset({"*"}))
            )
        foreign_client = client.post(
            "/v1/admin/clients",
            headers={"Authorization": "Bearer " + owner["token"]},
            json={"name": "Foreign reader", "scopes": ["context:read"]},
        ).json()["client"]["id"]
        with pytest.raises(ProjectBindingError):
            ProjectBindings(foreign.store).register_client(
                foreign_client, fixture.projects["borealis"]
            )


def test_cold_core_preserves_binding_replay_and_deletion(fixture: Fixture) -> None:
    capture, _ = fixture.principal("borealis", ["context:capture"])
    reader, receipt = fixture.principal("borealis", ["context:read"])
    payload = event()
    first = fixture.client.post("/v1/lifecycle/events", headers=capture, json=payload)
    assert first.status_code == 200
    fixture.core.close()
    with (
        CoreService(fixture.core.config) as cold,
        TestClient(create_app(fixture.core.config, service=cold)) as client,
    ):
        response = client.get("/v1/context/project-binding", headers=reader)
        assert response.json()["binding_id"] == receipt["binding_id"]
        replay = client.post("/v1/lifecycle/events", headers=capture, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["project_binding"] == first.json()["project_binding"]
        assert replay.json()["status"] == "replayed"
        result = client.post("/v1/context/bootstrap", headers=reader, json={}).json()
        record = next(item for item in result["items"] if "concise" in item["content"])
        cold.store.delete_record(record["id"], reason="Synthetic deletion")
    with (
        CoreService(fixture.core.config) as cold,
        TestClient(create_app(fixture.core.config, service=cold)) as client,
    ):
        replay = client.post("/v1/lifecycle/events", headers=capture, json=payload)
        assert replay.status_code == 200, replay.text
        result = client.post("/v1/context/bootstrap", headers=reader, json={}).json()
        assert all("concise" not in item["content"] for item in result["items"])


def test_schema_twenty_one_export_restores_memory_without_binding_authority(
    fixture: Fixture, tmp_path: Path
) -> None:
    fixture.principal("borealis", ["context:read"])
    package = tmp_path / "synthetic.atcexp"
    manifest = create_export(fixture.core.store.database_path, package, "synthetic passphrase")
    assert manifest["schema_version"] == 21
    assert NON_PORTABLE_SECURITY_TABLES.isdisjoint(manifest["tables"])
    destination = CoreStore(tmp_path / "restored.sqlite3")
    destination.initialize_vault()
    restore_export(package, destination.database_path, "synthetic passphrase")
    with destination.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM principal_project_bindings").fetchone()[0] == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM client_registrations").fetchone()[0] == 0
    assert destination.get_record(fixture.anchors["borealis"]).scopes == ["project:borealis"]


@pytest.mark.parametrize("interrupted_column", [False, True])
def test_schema_twenty_migration_preserves_explicit_legacy_clients(
    fixture: Fixture, interrupted_column: bool
) -> None:
    _, receipt = fixture.principal(None, ["context:read"])
    # Reconstruct only the prior synthetic schema, including the supported
    # interruption where ALTER committed but no migration marker exists.
    with fixture.core.store.transaction() as connection:
        for trigger in (
            "canonical_project_identity_immutable",
            "principal_project_binding_immutable",
            "project_binding_cannot_downgrade",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE principal_project_bindings")
        connection.execute("DROP TABLE canonical_projects")
        if not interrupted_column:
            connection.execute(
                "ALTER TABLE client_registrations DROP COLUMN project_binding_required"
            )
        connection.execute("DELETE FROM schema_migrations WHERE version=21")
        connection.execute("UPDATE vaults SET schema_version=20")
    assert fixture.core.store.migrate() == 21
    assert fixture.core.store.migrate() == 21
    principal = ClientPrincipal(receipt["client_id"], "Forged scope", frozenset({"*"}))
    assert ProjectBindings(fixture.core.store).resolve(principal) is None
    with fixture.core.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=21"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT project_binding_required FROM client_registrations WHERE id=?",
                (receipt["client_id"],),
            ).fetchone()[0]
            == 0
        )


def test_only_current_core_approved_remote_identity_retains_legacy_behavior(
    fixture: Fixture, tmp_path: Path
) -> None:
    bindings = ProjectBindings(fixture.core.store)
    principal = fixture.core.store.approve_remote_edge_client(
        "edge:synthetic-remote", name="Synthetic remote", scopes=["context:read"]
    )
    assert bindings.resolve(principal) is None
    with pytest.raises(ProjectBindingError):
        bindings.register_client(principal.id, fixture.projects["borealis"])
    with pytest.raises(ProjectBindingError):
        bindings.resolve(ClientPrincipal("edge:unapproved", "Forged", frozenset({"*"})))
    foreign = CoreStore(tmp_path / "foreign-remote.sqlite3")
    foreign.initialize_vault()
    with pytest.raises(ProjectBindingError):
        ProjectBindings(foreign).resolve(principal)
    fixture.core.store.revoke_remote_edge_client(principal.id)
    with pytest.raises(ProjectBindingError):
        bindings.resolve(principal)
