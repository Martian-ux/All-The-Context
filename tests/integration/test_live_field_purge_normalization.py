"""Canonical normalization cannot detach other supported live fields from raw erasure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.export import create_export, restore_export
from allthecontext.memory_policy import extract_live_user_claim
from allthecontext.storage import CoreStore
from fastapi.testclient import TestClient

from tests.integration.test_capture_purge_erasure import assert_no_plaintext
from tests.integration.test_decision_partial_runtime import capture, turn
from tests.integration.test_project_principal_bindings import Fixture, fixture

__all__ = ["fixture"]


@pytest.mark.parametrize(
    ("kind", "field", "statement"),
    [
        ("name", "name", "  My  name is Elena.  "),
        ("personal_context", "location", "  I  live in Raleigh.  "),
        ("personal_context", "ssn", "  My  SSN is 123-45-6789.  "),
        ("personal_context", "health", "  I  have asthma.  "),
        ("project", "current_project", "  My  current project is Lantern.  "),
        ("goal", "current_goal", "  My  goal is to learn Rust.  "),
        ("workflow", "tooling", "  I  use VSCode.  "),
    ],
    ids=["name", "location", "ssn", "health", "project", "goal", "workflow"],
)
def test_other_live_fields_keep_raw_erasure_after_canonical_normalization(
    fixture: Fixture, tmp_path: Path, kind: str, field: str, statement: str
) -> None:
    f = fixture
    claim = extract_live_user_claim(statement)
    assert claim is not None and claim.kind == kind and claim.attribute_key == field
    writer, _ = f.principal("borealis", ["context:capture"])
    receipt = capture(f.client, writer, turn(statement))
    observation = f.core.store.get_observation(receipt["observation_id"])
    assert observation.record_id is not None
    record_id = observation.record_id
    record = f.core.store.get_record(record_id)
    assert record.kind == kind and record.attribute_key == field and record.content == claim.content
    package = tmp_path / "fields.atcexp"
    passphrase = "synthetic field normalization export"
    create_export(f.core.store.database_path, package, passphrase, include_audit=True)
    f.core.store.purge("record", record_id, confirmation="PURGE RECORD " + record_id)
    assert_no_plaintext(f.core.store, [statement, claim.content])
    f.client.close()
    f.core.close()
    restored = CoreStore(f.core.store.database_path)
    restored.initialize_vault()
    restore_export(package, restored.database_path, passphrase)
    restored.rebuild_integrity_groups()
    assert_no_plaintext(restored, [statement, claim.content])
    restored.close()


def test_undeclared_extractor_field_cannot_form_live_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import memory_policy

    claim = memory_policy.LiveUserClaim(
        kind="interaction_preference",
        content="I prefer concise answers.",
        value="concise",
        attribute_key="undeclared_future_field",
        structured_value={"value": "concise"},
    )
    monkeypatch.setattr(memory_policy, "_extract_live_user_claim", lambda _: claim)
    assert extract_live_user_claim(claim.content) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database", "DuckDB"),
        ("offline_cache", "DuckDB"),
        ("implementation_language", "Rust"),
        ("frontend_framework", "Svelte"),
        ("api_format", "GraphQL"),
        ("test_runner", "pytest"),
        ("package_manager", "uv"),
    ],
)
def test_supported_decision_field_survives_erasure_restore_and_new_client_replay(
    fixture: Fixture, tmp_path: Path, field: str, value: str
) -> None:
    f = fixture
    statement = f"For this project, use {value} for the {field.replace('_', ' ')}."
    at = datetime.now(UTC) - timedelta(hours=1)
    writer, _ = f.principal("borealis", ["context:capture"])
    receipt = capture(f.client, writer, turn(statement, observed_at=at))
    observation = f.core.store.get_observation(receipt["observation_id"])
    assert observation.record_id is not None and observation.attribute_key == field
    record_id = observation.record_id
    package = tmp_path / "decision.atcexp"
    passphrase = "synthetic decision field export"
    create_export(f.core.store.database_path, package, passphrase, include_audit=True)
    f.core.store.purge("record", record_id, confirmation="PURGE RECORD " + record_id)
    f.client.close()
    f.core.close()
    restored = CoreStore(f.core.store.database_path)
    restored.initialize_vault()
    restore_export(package, restored.database_path, passphrase)
    restored.rebuild_integrity_groups()
    assert_no_plaintext(restored, [statement])
    restored.close()
    with (
        CoreService(f.core.config) as cold,
        TestClient(create_app(f.core.config, service=cold)) as client,
    ):
        reopened = Fixture(cold, client, f.owner, f.anchors, f.projects)
        fresh_writer, _ = reopened.principal("borealis", ["context:capture"])
        delayed = capture(client, fresh_writer, turn(statement, observed_at=at))
        assert delayed["reason"] == "capture_history_purged"
        assert_no_plaintext(cold.store, [statement])
