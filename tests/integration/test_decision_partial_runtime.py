"""Synthetic authenticated memory lifecycle, never an installed-client claim.

Inputs are independent development regressions. Frozen fixtures and holdouts
are not imported; transaction fault injection touches only disposable vaults.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any

import pytest
from allthecontext import claude_code_hook, codex_hook
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.hook_context import REFERENCE_FRAME
from allthecontext.http_client import ContextApiError
from allthecontext.memory_policy import ObservationOrigin
from allthecontext.models import CandidateInput, ContextRecordOut
from allthecontext.project_binding import ProjectBindings
from fastapi.testclient import TestClient

from tests.integration.test_project_principal_bindings import Fixture, event, fixture

__all__ = ["fixture"]
FIRST = "For this project, use SQLite for storage."
CORRECTION = "Correction: For this project, use Postgres for storage instead of SQLite."


def turn(
    content: str, *, observed_at: datetime | None = None, role: str = "user"
) -> dict[str, Any]:
    value = event(content)
    value["role"] = role
    value["observed_at"] = (observed_at or datetime.now(UTC) - timedelta(hours=1)).isoformat()
    return value


def capture(client: TestClient, writer: dict[str, str], value: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/lifecycle/events", headers=writer, json=value)
    assert response.status_code == 200, response.text
    return dict(response.json())


def record(fixture: Fixture, receipt: dict[str, Any]) -> ContextRecordOut:
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    assert observation.disposition.value in {"applied", "reinforced"}, observation
    assert observation.record_id is not None
    return fixture.core.store.get_record(observation.record_id)


def assert_tentative(fixture: Fixture, receipt: dict[str, Any], content: str) -> None:
    observation = fixture.core.store.get_observation(receipt["observation_id"])
    assert observation.disposition.value == "tentative", observation
    assert observation.record_id is None and observation.content == content


@pytest.mark.parametrize("app", ["codex", "claude"])
def test_public_capture_partial_bound_pack_cold_new_reader_and_actual_hook(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, app: str
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    foreign_writer, _ = fixture.principal("cedar", ["context:capture"])
    reader, _ = fixture.principal("borealis", ["context:read"])
    foreign_reader, _ = fixture.principal("cedar", ["context:read"])
    source = turn(FIRST)
    decision = record(fixture, capture(fixture.client, writer, source))
    foreign = record(fixture, capture(fixture.client, foreign_writer, turn(FIRST)))
    assert decision.entity_key == "project-decision:" + fixture.projects["borealis"]
    assert decision.scopes == ["project:borealis"]
    assert decision.source_type == "client_capture_formation"
    assert decision.observation_origin == "live_user_evidence"
    assert decision.explicit_user_statement is False
    assert decision.id != foreign.id
    with (
        CoreService(fixture.core.config) as cold,
        TestClient(create_app(fixture.core.config, service=cold)) as client,
    ):
        # A newly provisioned read principal after restart shares canonical scope,
        # not writer identity, CWD, display name or query project hints.
        new_fixture = Fixture(cold, client, fixture.owner, fixture.anchors, fixture.projects)
        new_reader, binding = new_fixture.principal("borealis", ["context:read"])
        query = "storage rollback"

        class Client:
            def bootstrap_context_core_only(self, request: dict[str, Any]) -> dict[str, Any]:
                response = client.post("/v1/context/bootstrap", headers=new_reader, json=request)
                if response.status_code != 200:
                    raise ContextApiError(response.status_code, "refused", "synthetic")
                return dict(response.json())

        pack = new_fixture.bootstrap(new_reader, query)
        assert decision.id in {item["id"] for item in pack["items"]}
        assert foreign.id not in {item["id"] for item in pack["items"]}
        assert all(item["scopes"] == ["project:borealis"] for item in pack["items"])
        assert not any("rollback" in item["content"].casefold() for item in pack["items"])
        hinted = new_fixture.bootstrap(new_reader, query, current_project="cedar")
        # A mismatched scoring hint may abstain; it never changes authority.
        assert foreign.id not in {item["id"] for item in hinted["items"]}
        assert all(item["scopes"] == ["project:borealis"] for item in hinted["items"])
        module = codex_hook if app == "codex" else claude_code_hook
        monkeypatch.setattr(module, "_hook_client", lambda: Client())
        delivered = (
            codex_hook._retrieve_hook_context(query, 8000)
            if app == "codex"
            else claude_code_hook._retrieve_hook_context(query)
        )
        blocks = [
            json.loads(part) for part in delivered.removeprefix(REFERENCE_FRAME).split("\n\n")
        ]
        refs = {part["atc_record"]["id"]: part for part in blocks if "atc_record" in part}
        assert refs[decision.id]["content"] == FIRST
        assert refs[decision.id]["atc_record"]["version"] == 1
        assert foreign.id not in refs
        assert [part["atc_delivery"]["state"] for part in blocks if "atc_delivery" in part] == [
            "records_returned"
        ]
        truth = client.get("/v1/context/truth/" + decision.id, headers=new_reader)
        assert truth.status_code == 200, truth.text
        assert truth.json()["record"]["source_reference"] == decision.source_reference
        assert any(e["content"] == FIRST for e in truth.json()["evidence"])
        replay = capture(client, writer, source)
        assert replay["status"] == "replayed"
        assert record(new_fixture, replay).version == 1
        ProjectBindings(cold.store).revoke(binding["binding_id"])
        unavailable = (
            codex_hook._retrieve_hook_context(query, 8000)
            if app == "codex"
            else claude_code_hook._retrieve_hook_context(query)
        )
        assert "retrieval_unavailable" in unavailable and FIRST not in unavailable
    assert fixture.client.get("/v1/context/" + decision.id, headers=reader).status_code == 200
    assert (
        fixture.client.get("/v1/context/" + decision.id, headers=foreign_reader).status_code == 404
    )


@pytest.mark.parametrize(
    ("statement", "attribute", "value"),
    [
        (FIRST, "database", "SQLite"),
        (
            "I have decided to use Rust for this project's implementation language.",
            "implementation_language",
            "Rust",
        ),
        (
            "For this project, I choose Svelte as the frontend framework.",
            "frontend_framework",
            "Svelte",
        ),
        ("For this project, my choice is GraphQL for the API style.", "api_format", "GraphQL"),
        ("For this project, use vitest for the test runner.", "test_runner", "Vitest"),
        ("For this project, use uv as the package manager.", "package_manager", "uv"),
    ],
)
def test_supported_roles_form_complete_user_decisions(
    fixture: Fixture, statement: str, attribute: str, value: str
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    formed = record(fixture, capture(fixture.client, writer, turn(statement)))
    assert formed.kind == "project_decision" and formed.attribute_key == attribute
    assert formed.content == statement
    assert formed.structured_value is not None and formed.structured_value["value"] == value


def test_correction_reinforcement_aba_chronology_and_current_source_history(
    fixture: Fixture,
) -> None:
    first_writer, _ = fixture.principal("borealis", ["context:capture"])
    second_writer, _ = fixture.principal("borealis", ["context:capture"])
    other_writer, _ = fixture.principal("cedar", ["context:capture"])
    start = datetime.now(UTC) - timedelta(hours=2)
    original = record(
        fixture, capture(fixture.client, first_writer, turn(FIRST, observed_at=start))
    )
    changed = record(
        fixture,
        capture(
            fixture.client,
            second_writer,
            turn(CORRECTION, observed_at=start + timedelta(minutes=2)),
        ),
    )
    assert changed.id == original.id and changed.version == 2
    assert changed.source_reference != original.source_reference
    assert changed.structured_value is not None
    assert changed.structured_value["expected_record_id"] == original.id
    assert changed.structured_value["expected_record_version"] == "1"
    equivalent = "I choose PostgreSQL for this project's database."
    reinforced = record(
        fixture,
        capture(
            fixture.client, first_writer, turn(equivalent, observed_at=start + timedelta(minutes=3))
        ),
    )
    assert reinforced.id == original.id and reinforced.version == 2
    assert (
        reinforced.content == CORRECTION and reinforced.source_reference == changed.source_reference
    )
    restored = "Correction: For this project, use SQLite for storage instead of PostgreSQL."
    back = record(
        fixture,
        capture(
            fixture.client, second_writer, turn(restored, observed_at=start + timedelta(minutes=4))
        ),
    )
    assert back.id == original.id and back.version == 3
    delayed = capture(
        fixture.client, first_writer, turn(CORRECTION, observed_at=start + timedelta(minutes=1))
    )
    assert_tentative(fixture, delayed, CORRECTION)
    assert (
        fixture.core.store.get_observation(delayed["observation_id"]).decision_reason
        == "project_decision_stale_correction"
    )
    foreign = capture(
        fixture.client, other_writer, turn(CORRECTION, observed_at=start + timedelta(minutes=5))
    )
    assert_tentative(fixture, foreign, CORRECTION)
    assert fixture.core.store.get_record(original.id) == back
    assert len(fixture.core.store.record_history(original.id)) == 3
    truth = fixture.core.store.get_memory_truth(original.id)
    assert {FIRST, CORRECTION, restored, equivalent} <= {e.content for e in truth.evidence}
    assert truth.record.source_reference == back.source_reference
    with CoreService(fixture.core.config) as cold:
        assert cold.store.get_record(original.id) == back
        assert len(cold.store.record_history(original.id)) == 3


def test_delayed_equivalent_reinforces_but_old_conflicting_turn_cannot_overwrite(
    fixture: Fixture,
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    second, _ = fixture.principal("borealis", ["context:capture"])
    start = datetime.now(UTC) - timedelta(hours=2)
    original = record(fixture, capture(fixture.client, writer, turn(FIRST, observed_at=start)))
    delayed = capture(
        fixture.client,
        second,
        turn(
            "I choose SQLite for this project's database.", observed_at=start - timedelta(minutes=1)
        ),
    )
    assert (
        fixture.core.store.get_observation(delayed["observation_id"]).disposition.value
        == "reinforced"
    )
    assert record(fixture, delayed).version == 1
    newer = "For this project, use PostgreSQL for the database."
    updated = record(
        fixture,
        capture(fixture.client, second, turn(newer, observed_at=start + timedelta(minutes=1))),
    )
    assert updated.id == original.id and updated.version == 2
    old = capture(fixture.client, writer, turn(FIRST, observed_at=start))
    observation = fixture.core.store.get_observation(old["observation_id"])
    assert observation.disposition.value == "ignored"
    assert fixture.core.store.get_record(original.id) == updated


@pytest.mark.parametrize(
    "content",
    [
        "For this project, do not use SQLite for storage.",
        "For this project, maybe use SQLite for storage.",
        "Alice said: For this project, use SQLite for storage.",
        "For this project, use SQLite for storage. I prefer concise answers.",
        "For this project, use SQLite for storage and React for frontend framework.",
        "For the other project, use SQLite for storage.",
        "For this project, I decided to use SQLite for storage last year.",
        "I use SQLite for this project's storage, but production did not deploy.",
        "I use SQLite for this project's storage last year.",
        "I use SQLite for the database?",
        "For this project, use MongoDB for storage.",
        "Correction: For this project, use PostgreSQL for storage instead of SQLite.",
    ],
)
def test_unsupported_mixed_historical_or_missing_predecessor_turn_cannot_fall_back(
    fixture: Fixture, content: str
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    receipt = capture(fixture.client, writer, turn(content))
    assert_tentative(fixture, receipt, content)
    with fixture.core.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_records WHERE entity_key=?",
                ("project-decision:" + fixture.projects["borealis"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE capture_event_id=? "
                "AND record_id IS NOT NULL",
                (receipt["capture_event_id"],),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("role", ["assistant", "tool", "imported"])
def test_non_user_decision_words_remain_raw(fixture: Fixture, role: str) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    assert_tentative(fixture, capture(fixture.client, writer, turn(FIRST, role=role)), FIRST)


def test_unbound_capture_has_no_decision_authority_and_personal_facets_stay_separate(
    fixture: Fixture,
) -> None:
    writer, _ = fixture.principal(None, ["context:capture"])
    assert_tentative(fixture, capture(fixture.client, writer, turn(FIRST)), FIRST)
    bound, _ = fixture.principal("borealis", ["context:capture"])
    preference = record(fixture, capture(fixture.client, bound, turn("I prefer concise answers.")))
    decision = record(fixture, capture(fixture.client, bound, turn(FIRST)))
    assert preference.kind == "interaction_preference"
    assert preference.entity_key == "user@project:" + fixture.projects["borealis"]
    assert decision.entity_key != preference.entity_key


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("I am working on a database migration.", "project"),
        ("My goal is to learn a programming language.", "goal"),
        ("My goal is to use a programming language for a side project.", "goal"),
    ],
)
def test_unrelated_project_and_goal_capture_is_preserved(
    fixture: Fixture, content: str, kind: str
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    formed = record(fixture, capture(fixture.client, writer, turn(content)))
    assert formed.kind == kind and formed.content == content


def test_unsupported_atomic_technical_workflow_does_not_bypass_decision_policy(
    fixture: Fixture,
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    content = "I use SQLite for storage."
    assert_tentative(fixture, capture(fixture.client, writer, turn(content)), content)


def test_deleted_decision_is_not_recreated_and_replay_does_not_revive_it(fixture: Fixture) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    reader, _ = fixture.principal("borealis", ["context:read"])
    payload = turn(FIRST)
    receipt = capture(fixture.client, writer, payload)
    original = record(fixture, receipt)
    fixture.core.store.delete_record(original.id, reason="Synthetic decision deletion")
    assert_tentative(fixture, capture(fixture.client, writer, turn(FIRST)), FIRST)
    replay = capture(fixture.client, writer, payload)
    assert replay["status"] == "replayed" and replay["observation_id"] == receipt["observation_id"]
    assert original.id not in {
        item["id"] for item in fixture.bootstrap(reader, "storage rollback")["items"]
    }
    assert fixture.core.store.get_record(original.id, include_deleted=True).deleted_at is not None


@pytest.mark.parametrize(
    "mutation",
    [
        "event_receipt",
        "event_hash",
        "binding_hash",
        "wrong_project",
        "source_disabled",
        "source_link",
        "acl",
        "future",
        "expired",
    ],
)
def test_missing_or_mismatched_current_lineage_abstains_without_changing_target(
    fixture: Fixture, mutation: str
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    other_writer, _ = fixture.principal("borealis", ["context:capture"])
    original = record(fixture, capture(fixture.client, writer, turn(FIRST)))
    with fixture.core.store.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM context_candidates WHERE source_reference=?",
            (original.source_reference,),
        ).fetchone()
        if mutation == "event_receipt":
            connection.execute(
                "UPDATE capture_events SET application_receipt=NULL WHERE id=?",
                (row["capture_event_id"],),
            )
        elif mutation == "event_hash":
            connection.execute(
                "UPDATE capture_events SET payload_hash=? WHERE id=?",
                ("0" * 64, row["capture_event_id"]),
            )
        elif mutation == "binding_hash":
            connection.execute(
                "UPDATE context_candidates SET capture_binding_hash=? WHERE id=?",
                ("0" * 64, row["id"]),
            )
        elif mutation == "wrong_project":
            connection.execute(
                "UPDATE context_records SET scopes_json=? WHERE id=?",
                (json.dumps(["project:cedar"]), original.id),
            )
        elif mutation == "source_disabled":
            connection.execute(
                "UPDATE capture_sources SET lifecycle_state='disabled' WHERE id=?",
                (row["capture_source_id"],),
            )
        elif mutation == "source_link":
            connection.execute(
                "DELETE FROM context_observation_links WHERE record_id=?", (original.id,)
            )
        elif mutation == "acl":
            connection.execute(
                "UPDATE context_records SET allowed_clients_json=? WHERE id=?",
                (json.dumps(["unrelated-synthetic-reader"]), original.id),
            )
        elif mutation == "future":
            connection.execute(
                "UPDATE context_records SET valid_from=? WHERE id=?",
                ((datetime.now(UTC) + timedelta(days=1)).isoformat(), original.id),
            )
        else:
            connection.execute(
                "UPDATE context_records SET expires_at=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(days=1)).isoformat(), original.id),
            )
    before = fixture.core.store.get_record(original.id)
    assert_tentative(
        fixture,
        capture(
            fixture.client,
            other_writer,
            turn(CORRECTION, observed_at=datetime.now(UTC) - timedelta(minutes=1)),
        ),
        CORRECTION,
    )
    assert fixture.core.store.get_record(original.id) == before
    assert len(fixture.core.store.record_history(original.id)) == 1


def test_keyed_owner_record_is_not_a_live_capture_predecessor(fixture: Fixture) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    candidate = fixture.core.store.add_candidate(
        CandidateInput(
            kind="project_decision",
            content=FIRST,
            entity_key="project-decision:" + fixture.projects["borealis"],
            attribute_key="database",
            scopes=["project:borealis"],
            structured_value={"value": "SQLite"},
        )
    )
    seeded = fixture.core.store.approve_candidate(candidate.id)
    assert_tentative(fixture, capture(fixture.client, writer, turn(CORRECTION)), CORRECTION)
    assert fixture.core.store.get_record(seeded.id) == seeded


def test_separate_store_concurrent_corrections_select_one_current_predecessor(
    fixture: Fixture,
) -> None:
    writers = [fixture.principal("borealis", ["context:capture"])[0] for _ in range(2)]
    start = datetime.now(UTC) - timedelta(hours=2)
    first = "For this project, use React as the frontend framework."
    original = record(fixture, capture(fixture.client, writers[0], turn(first, observed_at=start)))
    choices = ["Vue", "Svelte"]
    payloads = [
        turn(
            f"Correction: For this project, use {choice} as the frontend framework "
            "instead of React.",
            observed_at=start + timedelta(minutes=1),
        )
        for choice in choices
    ]
    barrier = Barrier(2)
    with (
        CoreService(fixture.core.config) as other,
        TestClient(create_app(fixture.core.config, service=other)) as client,
    ):

        def run(index: int) -> dict[str, Any]:
            barrier.wait(timeout=15)
            return capture(
                fixture.client if index == 0 else client, writers[index], payloads[index]
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(run, range(2)))
    observations = [fixture.core.store.get_observation(r["observation_id"]) for r in receipts]
    assert sorted(o.disposition.value for o in observations) == ["applied", "tentative"]
    assert fixture.core.store.get_record(original.id).version == 2
    assert len(fixture.core.store.record_history(original.id)) == 2
    for writer, payload, receipt in zip(writers, payloads, receipts, strict=True):
        replay = capture(fixture.client, writer, payload)
        assert (
            replay["status"] == "replayed" and replay["observation_id"] == receipt["observation_id"]
        )


def test_formation_fault_rolls_back_raw_capture_and_record_atomically(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    payload = turn(FIRST)
    original = fixture.core.store._evaluate_observation_tx

    def fail(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("origin") is ObservationOrigin.LIVE_USER_EVIDENCE:
            raise RuntimeError("synthetic formation fault")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(fixture.core.store, "_evaluate_observation_tx", fail)
        with pytest.raises(RuntimeError, match="synthetic formation fault"):
            capture(fixture.client, writer, payload)
    with fixture.core.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM capture_events WHERE provider_event_id=?",
                (payload["event_id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE content=?", (FIRST,)
            ).fetchone()[0]
            == 0
        )
    assert record(fixture, capture(fixture.client, writer, payload)).version == 1


def test_future_capture_stays_tentative_without_poisoning_decision_slot(fixture: Fixture) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    future = turn(FIRST, observed_at=datetime.now(UTC) + timedelta(days=1))
    assert_tentative(fixture, capture(fixture.client, writer, future), FIRST)
    assert record(fixture, capture(fixture.client, writer, turn(FIRST))).version == 1


@pytest.mark.parametrize("existing", [False, True])
def test_disabled_automatic_policy_prevents_creation_and_reinforcement(
    fixture: Fixture, existing: bool
) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    original = record(fixture, capture(fixture.client, writer, turn(FIRST))) if existing else None
    with fixture.core.store.transaction() as connection:
        connection.execute(
            "UPDATE memory_policies SET mode='manual' WHERE vault_id=?",
            (fixture.core.store.vault_id(),),
        )
    receipt = capture(fixture.client, writer, turn(FIRST))
    assert_tentative(fixture, receipt, FIRST)
    assert fixture.core.store.get_observation(receipt["observation_id"]).decision_reason == (
        "project_decision_automatic_disabled"
    )
    if original is not None:
        assert fixture.core.store.get_record(original.id) == original
        assert len(fixture.core.store.record_history(original.id)) == 1
    else:
        with fixture.core.store.connect() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM context_records WHERE kind='project_decision'"
                ).fetchone()[0]
                == 0
            )
