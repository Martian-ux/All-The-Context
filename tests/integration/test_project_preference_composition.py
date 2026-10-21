"""Synthetic intersections of bound-project authority and personal facet guards.

These exercise authenticated in-process HTTP, not installed harnesses or a model.
Frozen acceptance fixtures, grader and holdouts are deliberately not imported.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from allthecontext import claude_code_hook, codex_hook
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.hook_context import REFERENCE_FRAME
from allthecontext.http_client import ContextApiError
from allthecontext.models import CandidateInput, ContextRecordOut
from allthecontext.project_binding import ProjectBindings
from fastapi.testclient import TestClient

from tests.integration.test_project_principal_bindings import Fixture, fixture

__all__ = ["fixture"]
OLD_BOUND_RUNTIME = "42f067d834223a7039c68568d49b509a1c837544"
ROOT = Path(__file__).resolve().parents[2]


def payload(
    sequence: int, content: str, *, minute: int | None = None, actor: str = "synthetic-client"
) -> dict[str, Any]:
    identity = f"composition:{actor}:{sequence}:{content}"
    observed = datetime(2026, 10, 2, 10, tzinfo=UTC) + timedelta(
        minutes=sequence if minute is None else minute
    )
    return {
        "event_id": "composition-" + actor + "-" + str(sequence),
        "idempotency_key": str(
            uuid.UUID(bytes=uuid.uuid5(uuid.NAMESPACE_URL, identity).bytes, version=4)
        ),
        "session_id": "synthetic-composition",
        "conversation_id": "synthetic-composition",
        "sequence": sequence,
        "role": "user",
        "content": content,
        "observed_at": observed.isoformat(),
    }


def capture(
    client: TestClient,
    writer: dict[str, str],
    sequence: int,
    content: str,
    *,
    minute: int | None = None,
) -> dict[str, Any]:
    # Distinct synthetic principals emit distinct events; replay is stable per principal.
    # The ephemeral token itself is never copied into event data.
    actor = uuid.uuid5(uuid.NAMESPACE_URL, writer["Authorization"]).hex
    response = client.post(
        "/v1/lifecycle/events",
        headers=writer,
        json=payload(sequence, content, minute=minute, actor=actor),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def formed(fixture: Fixture, receipt: dict[str, Any]) -> ContextRecordOut:
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    assert observation.disposition.value == "applied", observation
    assert observation.record_id is not None
    return fixture.core.store.get_record(observation.record_id)


def legacy(
    fixture: Fixture,
    project: str | None,
    content: str,
    value: str,
    *,
    deleted: bool = False,
) -> ContextRecordOut:
    candidate = fixture.core.store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            entity_key="user" if project is None else "user@project:" + fixture.projects[project],
            attribute_key="preference",
            scopes=[] if project is None else ["project:" + project],
            content=content,
            structured_value={"value": value},
        )
    )
    record = fixture.core.store.approve_candidate(candidate.id)
    if deleted:
        fixture.core.store.delete_record(record.id, reason="Synthetic legacy deletion")
    return fixture.core.store.get_record(record.id, include_deleted=True)


def test_project_facets_correct_independently_and_cannot_use_other_project_predecessor(
    fixture: Fixture,
) -> None:
    writers = {name: fixture.principal(name, ["context:capture"])[0] for name in fixture.projects}
    readers = {name: fixture.principal(name, ["context:read"])[0] for name in fixture.projects}
    values = {
        "borealis": ["detailed explanations", "paragraph replies", "English replies", "herbal tea"],
        "cedar": ["concise explanations", "numbered replies", "Spanish replies", "cold coffee"],
    }
    records: dict[str, list[ContextRecordOut]] = {}
    for project, preferences in values.items():
        records[project] = [
            formed(fixture, capture(fixture.client, writers[project], i, "I prefer " + text + "."))
            for i, text in enumerate(preferences, 1)
        ]
        assert {record.attribute_key for record in records[project]} == {
            "response_style",
            "response_layout",
            "response_language",
            "beverage",
        }
        assert all(
            record.entity_key == "user@project:" + fixture.projects[project]
            and record.scopes == ["project:" + project]
            for record in records[project]
        )
    correction = "Correction: I prefer lucid explanations instead of detailed explanations."
    result = capture(fixture.client, writers["borealis"], 5, correction)
    changed = formed(fixture, result)
    assert changed.id == records["borealis"][0].id and changed.version == 2
    assert changed.structured_value is not None
    assert changed.structured_value["expected_record_id"] == changed.id
    assert changed.structured_value["expected_record_version"] == "1"
    wrong = capture(fixture.client, writers["cedar"], 5, correction)
    refused = fixture.core.store.get_observation(wrong["observation_id"])
    assert refused.record_id is None and refused.decision_reason == "personal_preference_stale"
    for project in records:
        for record in records[project][1:]:
            assert fixture.core.store.get_record(record.id) == record
        pack = fixture.bootstrap(
            readers[project], "other project preferences", current_project="other"
        )
        preferences = [item for item in pack["items"] if item["kind"] == "interaction_preference"]
        assert len(preferences) == 4  # Mandatory preference tier also obeys the partition.
        assert all(item["scopes"] == ["project:" + project] for item in pack["items"])
        foreign = "cedar" if project == "borealis" else "borealis"
        assert not {record.id for record in records[foreign]} & {
            item["id"] for item in pack["items"]
        }
        for record in records[foreign]:
            for route in ("/v1/context/", "/v1/context/truth/"):
                assert (
                    fixture.client.get(route + record.id, headers=readers[project]).status_code
                    == 404
                )
    assert fixture.core.store.get_record(records["cedar"][0].id) == records["cedar"][0]
    replay = capture(fixture.client, writers["borealis"], 5, correction)
    assert replay["status"] == "replayed" and replay["project_binding"] == result["project_binding"]
    assert fixture.core.store.get_record(changed.id).version == 2


def test_delayed_equivalent_capture_reinforces_only_its_bound_partition(fixture: Fixture) -> None:
    first, _ = fixture.principal("borealis", ["context:capture"])
    second, _ = fixture.principal("borealis", ["context:capture"])
    other, _ = fixture.principal("cedar", ["context:capture"])
    text = "I prefer meticulous explanations."
    record = formed(fixture, capture(fixture.client, first, 1, text, minute=20))
    independent = formed(fixture, capture(fixture.client, other, 1, text, minute=20))
    receipt = capture(fixture.client, second, 2, text, minute=10)
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    assert observation.disposition.value == "reinforced" and observation.record_id == record.id
    delayed = fixture.core.store.get_record(record.id)
    assert delayed.id == record.id and delayed.version == record.version
    assert independent.id != record.id
    assert delayed.structured_value is not None
    assert "expected_record_id" not in delayed.structured_value
    assert len(fixture.core.store.record_history(record.id)) == 1


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_corrected_bound_facets_reach_actual_hook_helper_with_resolvable_provenance(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, app: str
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    foreign_writer, _ = fixture.principal("cedar", ["context:capture"])
    reader, binding = fixture.principal("borealis", ["context:read"])
    first = formed(fixture, capture(fixture.client, writer, 1, "I prefer detailed explanations."))
    layout = formed(fixture, capture(fixture.client, writer, 2, "I prefer numbered replies."))
    foreign = formed(
        fixture, capture(fixture.client, foreign_writer, 1, "I prefer Spanish replies.")
    )
    correction = "Correction: I prefer lucid explanations instead of detailed explanations."
    corrected = formed(fixture, capture(fixture.client, writer, 3, correction))
    assert corrected.id == first.id and corrected.version == 2

    class Client:
        def bootstrap_context_core_only(self, request: dict[str, Any]) -> dict[str, Any]:
            response = fixture.client.post("/v1/context/bootstrap", headers=reader, json=request)
            if response.status_code != 200:
                raise ContextApiError(response.status_code, "refused", "synthetic-private")
            return dict(response.json())

    module = codex_hook if app == "codex" else claude_code_hook
    monkeypatch.setattr(module, "_hook_client", lambda: Client())
    delivered = (
        codex_hook._retrieve_hook_context("cedar language", 8000)
        if app == "codex"
        else claude_code_hook._retrieve_hook_context("cedar language")
    )
    blocks = [json.loads(part) for part in delivered.removeprefix(REFERENCE_FRAME).split("\n\n")]
    references = {part["atc_record"]["id"]: part for part in blocks if "atc_record" in part}
    assert {corrected.id, layout.id} <= references.keys()
    assert foreign.id not in references and "Spanish" not in delivered
    assert references[corrected.id]["atc_record"]["version"] == 2
    truth = fixture.client.get("/v1/context/truth/" + corrected.id, headers=reader)
    assert truth.status_code == 200, truth.text
    assert truth.json()["record"]["content"] == references[corrected.id]["content"] == correction
    assert {evidence["content"] for evidence in truth.json()["evidence"]} >= {
        first.content,
        correction,
    }
    ProjectBindings(fixture.core.store).revoke(binding["binding_id"])
    unavailable = (
        codex_hook._retrieve_hook_context("cedar language", 8000)
        if app == "codex"
        else claude_code_hook._retrieve_hook_context("cedar language")
    )
    assert "retrieval_unavailable" in unavailable
    assert "lucid" not in unavailable and "Spanish" not in unavailable


@pytest.mark.parametrize("same_project", [False, True])
def test_separate_stores_serialize_corrections_per_project(
    fixture: Fixture, same_project: bool
) -> None:
    one, _ = fixture.principal("borealis", ["context:capture"])
    two, _ = fixture.principal("borealis" if same_project else "cedar", ["context:capture"])
    text = "I prefer detailed explanations."
    first = formed(fixture, capture(fixture.client, one, 1, text))
    second = first if same_project else formed(fixture, capture(fixture.client, two, 1, text))
    barrier = Barrier(2)
    corrections = [
        "Correction: I prefer lucid explanations instead of detailed explanations.",
        "Correction: I prefer compact explanations instead of detailed explanations.",
    ]
    with (
        CoreService(fixture.core.config) as other_core,
        TestClient(create_app(fixture.core.config, service=other_core)) as other_client,
    ):

        def run(index: int) -> dict[str, Any]:
            barrier.wait(timeout=15)
            return capture(
                fixture.client if index == 0 else other_client,
                one if index == 0 else two,
                index + 2,
                corrections[index],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(run, range(2)))
        observations = [fixture.core.store.get_observation(r["observation_id"]) for r in receipts]
        assert sorted(o.disposition.value for o in observations) == (
            ["applied", "tentative"] if same_project else ["applied", "applied"]
        )
        if same_project:
            assert [o.decision_reason for o in observations if o.record_id is None] == [
                "personal_preference_stale"
            ]
        for record in (first, second):
            assert fixture.core.store.get_record(record.id).version == 2
            assert len(fixture.core.store.record_history(record.id)) == 2
        for index, receipt in enumerate(receipts):
            replay = capture(
                fixture.client, one if index == 0 else two, index + 2, corrections[index]
            )
            assert replay["observation_id"] == receipt["observation_id"]
            assert replay["project_binding"] == receipt["project_binding"]


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    ("text", "value", "blocked"),
    [
        ("I prefer herbal tea.", "herbal tea", False),
        ("I prefer detailed explanations.", "detailed explanations", True),
        ("I prefer relaxing evenings.", "relaxing evenings", True),
        ("I prefer herbal tea.", "numbered replies", True),
    ],
)
def test_project_legacy_subject_guard_preserves_history_and_other_projects(
    fixture: Fixture, deleted: bool, text: str, value: str, blocked: bool
) -> None:
    prior = legacy(fixture, "borealis", text, value, deleted=deleted)
    history = fixture.core.store.record_history(prior.id)
    one, _ = fixture.principal("borealis", ["context:capture"])
    two, _ = fixture.principal("cedar", ["context:capture"])
    statement = "I prefer lucid explanations."
    receipt = capture(fixture.client, one, 1, statement)
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    if blocked:
        assert (
            observation.record_id is None
            and observation.decision_reason == "personal_preference_legacy"
        )
    else:
        assert formed(fixture, receipt).attribute_key == "response_style"
    assert (
        formed(fixture, capture(fixture.client, two, 1, statement)).attribute_key
        == "response_style"
    )
    assert fixture.core.store.get_record(prior.id, include_deleted=True) == prior
    assert fixture.core.store.record_history(prior.id) == history


def test_global_legacy_never_enters_bound_pack_or_supplies_a_predecessor(fixture: Fixture) -> None:
    prior = legacy(fixture, None, "I prefer detailed explanations.", "detailed explanations")
    writer, _ = fixture.principal("borealis", ["context:capture"])
    reader, _ = fixture.principal("borealis", ["context:read"])
    receipt = capture(
        fixture.client,
        writer,
        1,
        "Correction: I prefer lucid explanations instead of detailed explanations.",
    )
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    assert (
        observation.record_id is None
        and observation.decision_reason == "personal_preference_no_predecessor"
    )
    current = formed(fixture, capture(fixture.client, writer, 2, "I prefer lucid explanations."))
    assert current.id != prior.id
    assert prior.id not in {item["id"] for item in fixture.bootstrap(reader)["items"]}
    assert fixture.core.store.get_record(prior.id) == prior


@pytest.mark.parametrize(
    "boundary", ["global_scope", "wrong_project", "mixed_project", "denied", "source", "kind"]
)
def test_same_entity_does_not_relax_target_scope_source_acl_or_kind(
    fixture: Fixture, boundary: str
) -> None:
    writer, receipt = fixture.principal("borealis", ["context:capture"])
    source_id = None
    if boundary == "source":
        source_id = fixture.core.store.add_source(
            b"Synthetic imported preference", source_service="synthetic", source_type="text"
        ).id
    scopes = {
        "global_scope": [],
        "wrong_project": ["project:cedar"],
        "mixed_project": ["project:borealis", "project:cedar"],
    }.get(boundary, ["project:borealis"])
    candidate = fixture.core.store.add_candidate(
        CandidateInput(
            kind="workflow" if boundary == "kind" else "interaction_preference",
            entity_key="user@project:" + fixture.projects["borealis"],
            attribute_key="response_style",
            scopes=scopes,
            source_id=source_id,
            denied_clients=[receipt["client_id"]] if boundary == "denied" else [],
            content="I prefer detailed explanations.",
            structured_value={"value": "detailed explanations"},
            observed_at="2026-10-02T10:00:00Z",
        )
    )
    prior = fixture.core.store.approve_candidate(candidate.id)
    result = capture(
        fixture.client,
        writer,
        1,
        "Correction: I prefer lucid explanations instead of detailed explanations.",
    )
    observation = fixture.core.store.get_observation(result["observation_id"])
    assert (
        observation.record_id is None
        and observation.decision_reason == "personal_preference_boundary"
    )
    assert fixture.core.store.get_record(prior.id) == prior
    assert len(fixture.core.store.record_history(prior.id)) == 1


def test_project_tombstone_and_replay_survive_restart_without_blocking_other_project(
    fixture: Fixture,
) -> None:
    one, _ = fixture.principal("borealis", ["context:capture"])
    two, _ = fixture.principal("cedar", ["context:capture"])
    reader, _ = fixture.principal("borealis", ["context:read"])
    statement = "I prefer detailed explanations."
    first_receipt = capture(fixture.client, one, 1, statement)
    first = formed(fixture, first_receipt)
    correction = "Correction: I prefer lucid explanations instead of detailed explanations."
    corrected = formed(fixture, capture(fixture.client, one, 2, correction))
    assert corrected.id == first.id and corrected.version == 2
    fixture.core.store.delete_record(first.id, reason="Synthetic preference deletion")
    tombstone = fixture.core.store.get_record(first.id, include_deleted=True)
    fixture.core.close()
    with (
        CoreService(fixture.core.config) as cold,
        TestClient(create_app(fixture.core.config, service=cold)) as client,
    ):
        replay = capture(client, one, 1, statement)
        assert replay["status"] == "replayed"
        assert replay["project_binding"] == first_receipt["project_binding"]
        for sequence, content in enumerate(
            (
                "I prefer meticulous explanations.",
                "Correction: I prefer compact explanations instead of lucid explanations.",
            ),
            3,
        ):
            blocked = capture(client, one, sequence, content)
            observation = cold.store.get_observation(blocked["observation_id"])
            assert observation.record_id is None
            assert observation.decision_reason == "personal_preference_slot_blocked"
        other = capture(client, two, 1, statement)
        assert cold.store.get_observation(other["observation_id"]).record_id not in {None, first.id}
        pack = client.post("/v1/context/bootstrap", headers=reader, json={}).json()
        assert not [item for item in pack["items"] if item["kind"] == "interaction_preference"]
        assert cold.store.get_record(first.id, include_deleted=True) == tombstone


@pytest.mark.parametrize("failure", ["missing", "revoked"])
def test_required_binding_failure_rejects_correction_before_staging(
    fixture: Fixture, failure: str
) -> None:
    writer, receipt = fixture.principal("borealis", ["context:capture"])
    record = formed(fixture, capture(fixture.client, writer, 1, "I prefer detailed explanations."))
    if failure == "missing":
        with fixture.core.store.transaction() as connection:
            connection.execute(
                "DELETE FROM principal_project_bindings WHERE id=?", (receipt["binding_id"],)
            )
    else:
        ProjectBindings(fixture.core.store).revoke(receipt["binding_id"])
    with fixture.core.store.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0]
    response = fixture.client.post(
        "/v1/lifecycle/events",
        headers=writer,
        json=payload(
            2, "Correction: I prefer lucid explanations instead of detailed explanations."
        ),
    )
    assert response.status_code == 403, response.text
    assert fixture.core.store.get_record(record.id) == record
    with fixture.core.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == before


OLD_BOUND_CAPTURE = """
import json, sys, uuid
from pathlib import Path
import keyring
from keyring.backends.null import Keyring
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.core.app import create_app
from fastapi.testclient import TestClient
keyring.set_keyring(Keyring())
root = Path(sys.argv[1])
config = CoreConfig.in_directory(root, require_auth=True)
with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
    setup = client.post('/v1/setup', json={'name': 'Synthetic old owner'}).json()
    owner = {'Authorization': 'Bearer ' + setup['token']}
    anchor = client.post('/v1/ingestion/propose', headers=owner, json={
        'kind': 'project', 'content': 'Synthetic old project', 'scopes': ['project:old'],
        'entity_key': 'old', 'attribute_key': 'identity',
        'explicit_user_statement': True}).json()['record_id']
    identity = client.post('/v1/admin/project-identities', headers=owner,
        json={'anchor_record_id': anchor}).json()['canonical_project_id']
    clients = {}
    for purpose, scope in [('writer', 'context:capture'), ('reader', 'context:read')]:
        created = client.post('/v1/admin/clients', headers=owner,
            json={'name': 'Synthetic old ' + purpose, 'scopes': [scope]}).json()
        clients[purpose] = {'Authorization': 'Bearer ' + created['token']}
        result = client.post('/v1/admin/project-bindings', headers=owner,
            json={'client_id': created['client']['id'], 'canonical_project_id': identity})
        assert result.status_code == 200, result.text
    receipts = []
    payloads = []
    for i, text in enumerate(['I prefer detailed explanations.', 'I prefer herbal tea.'], 1):
        payload = {'event_id': 'old-bound-' + str(i), 'idempotency_key': str(uuid.uuid4()),
            'session_id': 'synthetic-old', 'conversation_id': 'synthetic-old', 'sequence': i,
            'role': 'user', 'content': text, 'observed_at': '2026-10-02T10:0' + str(i) + ':00Z'}
        response = client.post('/v1/lifecycle/events', headers=clients['writer'], json=payload)
        assert response.status_code == 200, response.text
        receipt = response.json()
        observation = core.store.get_observation(receipt['observation_id'])
        record = core.store.get_record(observation.record_id)
        assert record.attribute_key == ('response_style' if i == 1 else 'preference')
        assert record.entity_key == 'user@project:' + identity
        assert record.scopes == ['project:old']
        receipts.append({'response': receipt, 'record': record.model_dump(mode='json')})
        payloads.append(payload)
    state = {'clients': clients, 'identity': identity, 'receipts': receipts,
        'payloads': payloads}
    (root / 'synthetic-bound-state.json').write_text(json.dumps(state), encoding='utf-8')
"""


def test_actual_old_bound_runtime_upgrade_preserves_identity_history_and_legacy_guard(
    tmp_path: Path,
) -> None:
    # Export runtime only, never acceptance fixtures or holdouts.
    archive = subprocess.run(
        ["git", "archive", "--format=tar", OLD_BOUND_RUNTIME, "packages/allthecontext/src"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    old_code = tmp_path / "old-code"
    old_code.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(old_code, filter="data")
    vault = tmp_path / "synthetic-bound-vault"
    env = {
        **os.environ,
        "PYTHONPATH": str(old_code / "packages/allthecontext/src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    seeded = subprocess.run(
        [sys.executable, "-B", "-c", OLD_BOUND_CAPTURE, str(vault)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert seeded.returncode == 0, seeded.stderr
    state = json.loads((vault / "synthetic-bound-state.json").read_text(encoding="utf-8"))
    config = CoreConfig.in_directory(vault, require_auth=True)
    with CoreService(config) as core, TestClient(create_app(config, service=core)) as client:
        prior = [ContextRecordOut.model_validate(r["record"]) for r in state["receipts"]]
        histories = [core.store.record_history(record.id) for record in prior]
        for old_payload, receipt in zip(state["payloads"], state["receipts"], strict=True):
            replay = client.post(
                "/v1/lifecycle/events", headers=state["clients"]["writer"], json=old_payload
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["status"] == "replayed"
            assert replay.json()["project_binding"] == receipt["response"]["project_binding"]
        correction = "Correction: I prefer lucid explanations instead of detailed explanations."
        accepted = capture(client, state["clients"]["writer"], 3, correction)
        observation = core.store.get_observation(accepted["observation_id"])
        assert observation.record_id == prior[0].id and observation.disposition.value == "applied"
        current = core.store.get_record(prior[0].id)
        assert current.version == 2 and current.entity_key == "user@project:" + state["identity"]
        assert current.scopes == ["project:old"]
        assert core.store.record_history(prior[0].id)[:1] == histories[0]
        layout = capture(client, state["clients"]["writer"], 4, "I prefer numbered replies.")
        assert core.store.get_observation(layout["observation_id"]).record_id is not None
        beverage = capture(client, state["clients"]["writer"], 5, "I prefer cold coffee.")
        blocked = core.store.get_observation(beverage["observation_id"])
        assert blocked.record_id is None and blocked.decision_reason == "personal_preference_legacy"
        assert core.store.get_record(prior[1].id) == prior[1]
        assert core.store.record_history(prior[1].id) == histories[1]
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        pack = client.post(
            "/v1/context/bootstrap", headers=state["clients"]["reader"], json={}
        ).json()
        assert {
            item["attribute_key"]
            for item in pack["items"]
            if item["kind"] == "interaction_preference"
        } == {"response_style", "response_layout", "preference"}
        assert all(item["scopes"] == ["project:old"] for item in pack["items"])
        assert cold.store.get_record(prior[0].id).version == 2
        assert cold.store.get_record(prior[1].id) == prior[1]
