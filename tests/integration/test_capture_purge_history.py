"""Synthetic content-free purge fences: chronology, partitioning and recovery."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from allthecontext import export as portable_export
from allthecontext.capture_purge_barriers import TABLE, validate_row
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.export import create_export, restore_export
from allthecontext.recovery_admin import carry_forward_purge_tombstones
from allthecontext.storage import CoreStore
from fastapi.testclient import TestClient

from tests.integration.test_bound_named_project_decisions import named_project
from tests.integration.test_capture_purge_erasure import assert_no_plaintext
from tests.integration.test_decision_partial_runtime import capture, turn
from tests.integration.test_project_principal_bindings import Fixture, fixture

__all__ = ["fixture"]
PASSPHRASE = "synthetic purge portability passphrase"


def _rewrite_export(
    package: Path,
    destination: Path,
    mutate: Callable[[dict[str, bytes], dict[str, Any]], None],
) -> Path:
    # The synthetic operator knows the package passphrase. Re-authenticate a
    # malformed package so rejection exercises the graph boundary, not crypto.
    with TemporaryDirectory(dir=destination.parent) as temporary:
        payload = Path(temporary) / "payload.zip"
        portable_export._decrypt_file(package, payload, PASSPHRASE)
        with zipfile.ZipFile(payload) as incoming:
            members = {info.filename: incoming.read(info.filename) for info in incoming.infolist()}
        manifest = json.loads(members["manifest.json"])
        mutate(members, manifest)
        for name in manifest["sha256"]:
            manifest["sha256"][name] = hashlib.sha256(members[name]).hexdigest()
        members["manifest.json"] = json.dumps(manifest).encode()
        rewritten = Path(temporary) / "rewritten.zip"
        with zipfile.ZipFile(rewritten, "w") as output:
            for name, content in members.items():
                output.writestr(name, content)
        portable_export._encrypt_file(rewritten, destination, PASSPHRASE)
    return destination


def prepare(f: Fixture, kind: str, project: str | None = "quarry") -> tuple[str, str, datetime]:
    if project == "quarry" and project not in f.projects:
        named_project(f, "Quarry", "quarry")
    statement = (
        "I prefer quick responses."
        if kind == "preference"
        else "For Quarry, use DuckDB for offline cache."
    )
    writer, _ = f.principal(project, ["context:capture"])
    prior = datetime.now(UTC) - timedelta(hours=3)
    receipt = capture(f.client, writer, turn(statement, observed_at=prior))
    record_id = f.core.store.get_observation(receipt["observation_id"]).record_id
    assert record_id is not None
    f.core.store.purge(
        "record",
        record_id,
        confirmation=f.core.store.purge_confirmation_phrase("record", record_id),
    )
    with f.core.store.connect() as connection:
        fences = connection.execute(f"SELECT * FROM {TABLE}").fetchall()
        assert len(fences) == 1
        validate_row(fences[0])
        cutoff = datetime.fromisoformat(fences[0]["purged_at"].replace("Z", "+00:00"))
    assert_no_plaintext(f.core.store, [statement])
    return statement, record_id, cutoff


@pytest.mark.parametrize("kind", ["preference", "decision"])
@pytest.mark.parametrize("timing", ["old", "equal", "offset", "future", "new"])
def test_fence_survives_cold_rebuild_and_respects_chronology(
    fixture: Fixture, kind: str, timing: str
) -> None:
    f = fixture
    statement, old_id, cutoff = prepare(f, kind)
    f.client.close()
    f.core.close()
    with (
        CoreService(f.core.config) as cold,
        TestClient(create_app(f.core.config, service=cold)) as client,
    ):
        reopened = Fixture(cold, client, f.owner, f.anchors, f.projects)
        writer, _ = reopened.principal("quarry", ["context:capture"])
        cold.store.rebuild_integrity_groups()
        at = {
            "old": cutoff - timedelta(seconds=1),
            "equal": cutoff,
            "offset": cutoff.astimezone(timezone(timedelta(hours=5, minutes=30))),
            "future": datetime.now(UTC) + timedelta(days=1),
            "new": datetime.now(UTC),
        }[timing]
        event = turn(statement, observed_at=at)
        receipt = capture(client, writer, event)
        observation = cold.store.get_observation(receipt["observation_id"])
        if timing == "new":
            assert observation.record_id is not None and observation.record_id != old_id
            assert observation.disposition.value == "applied"
        else:
            assert receipt["reason"] == "capture_history_purged"
            assert observation.record_id is None and observation.disposition.value == "ignored"
            assert observation.kind == "purged_capture_receipt"
            assert observation.entity_key is None and observation.attribute_key is None
            assert_no_plaintext(cold.store, [statement])
            with cold.store.connect() as connection:
                row = connection.execute(
                    "SELECT normalized_payload_json,payload_hash FROM capture_events WHERE id=?",
                    (receipt["capture_event_id"],),
                ).fetchone()
                assert row["payload_hash"] == "0" * 64
                assert set(json.loads(row["normalized_payload_json"])) == {
                    "purged_capture_receipt",
                    "project_binding",
                }
            retry = capture(client, writer, {**event, "content": "I prefer dark mode."})
            assert retry["status"] == "replayed" and retry["reason"] == "capture_history_purged"
            assert retry["observation_id"] == receipt["observation_id"]
            assert_no_plaintext(cold.store, [statement, "I prefer dark mode."])


def test_fence_does_not_cross_project_or_facet_and_new_choices_can_change(fixture: Fixture) -> None:
    f = fixture
    statement, _, cutoff = prepare(f, "preference", "borealis")
    for project, text in (("cedar", statement), ("borealis", "I prefer dark mode.")):
        writer, _ = f.principal(project, ["context:capture"])
        receipt = capture(f.client, writer, turn(text, observed_at=cutoff - timedelta(seconds=1)))
        assert f.core.store.get_observation(receipt["observation_id"]).record_id is not None
    writer, _ = f.principal("borealis", ["context:capture"])
    current = capture(
        f.client, writer, turn("I prefer thorough responses.", observed_at=datetime.now(UTC))
    )
    assert f.core.store.get_observation(current["observation_id"]).record_id is not None
    delayed = capture(f.client, writer, turn(statement, observed_at=cutoff - timedelta(seconds=1)))
    assert delayed["reason"] == "capture_history_purged"
    current_id = f.core.store.get_observation(current["observation_id"]).record_id
    assert current_id is not None
    record = f.core.store.get_record(current_id)
    assert record.content == "I prefer thorough responses."


def test_unbound_fence_is_shared_across_new_unbound_clients(fixture: Fixture) -> None:
    f = fixture
    statement, _, cutoff = prepare(f, "preference", None)
    writer, _ = f.principal(None, ["context:capture"])
    receipt = capture(f.client, writer, turn(statement, observed_at=cutoff))
    assert receipt["reason"] == "capture_history_purged"
    assert_no_plaintext(f.core.store, [statement])


@pytest.mark.parametrize("kind", ["preference", "decision"])
@pytest.mark.parametrize("include_sources", [False, True])
def test_export_restore_rebinds_fence_by_anchor_and_carries_it_into_recovery(
    fixture: Fixture, tmp_path: Path, kind: str, include_sources: bool
) -> None:
    f = fixture
    statement, _, cutoff = prepare(f, kind)
    archive = tmp_path / "synthetic.atcexp"
    manifest = create_export(
        f.core.store.database_path, archive, "synthetic passphrase", include_sources=include_sources
    )
    assert manifest["tables"][TABLE] == 1 and manifest["schema_version"] == 22
    config = CoreConfig.in_directory(tmp_path / "restored", require_auth=True)
    destination = CoreStore(config.database_path)
    destination.initialize_vault()
    restore_export(archive, config.database_path, "synthetic passphrase")
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        owner = client.post("/v1/setup", json={"name": "Synthetic restored owner"}).json()
        recovered = Fixture(
            cold, client, {"Authorization": "Bearer " + owner["token"]}, f.anchors, {}
        )
        identity = client.post(
            "/v1/admin/project-identities",
            headers=recovered.owner,
            json={"anchor_record_id": f.anchors["quarry"]},
        )
        assert identity.status_code == 200, identity.text
        recovered.projects["quarry"] = identity.json()["canonical_project_id"]
        assert recovered.projects["quarry"] != f.projects["quarry"]
        writer, _ = recovered.principal("quarry", ["context:capture"])
        cold.store.rebuild_integrity_groups()
        receipt = capture(client, writer, turn(statement, observed_at=cutoff))
        assert receipt["reason"] == "capture_history_purged"
        assert_no_plaintext(cold.store, [statement])
    isolated = CoreStore(tmp_path / "isolated.sqlite3")
    isolated.migrate()
    evidence = carry_forward_purge_tombstones(config.database_path, isolated.database_path)
    assert evidence["carried_client_capture_purge_barriers"] == 1
    with isolated.connect() as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("facet_id", "erased-text"),
        ("partition_id", "project-name"),
        ("record_id", "deleted-value"),
        ("purged_at", "2026-10-11T00:00:00"),
    ],
)
def test_portable_fence_closed_shape_rejects_content_and_naive_time(
    fixture: Fixture, field: str, value: Any
) -> None:
    prepare(fixture, "preference")
    with fixture.core.store.connect() as connection:
        row = dict(connection.execute(f"SELECT * FROM {TABLE}").fetchone())
    row[field] = value
    with pytest.raises(ValueError):
        validate_row(row)


@pytest.mark.parametrize("kind", ["preference", "decision"])
def test_restored_capture_can_be_purged_and_then_block_changed_id_history(
    fixture: Fixture, tmp_path: Path, kind: str
) -> None:
    f = fixture
    named_project(f, "Quarry", "quarry")
    statement = (
        "I prefer quick responses."
        if kind == "preference"
        else "For Quarry, use DuckDB for offline cache."
    )
    writer, _ = f.principal("quarry", ["context:capture"])
    at = datetime.now(UTC) - timedelta(hours=2)
    receipt = capture(f.client, writer, turn(statement, observed_at=at))
    record_id = f.core.store.get_observation(receipt["observation_id"]).record_id
    assert record_id is not None
    archive = tmp_path / "before.atcexp"
    create_export(f.core.store.database_path, archive, PASSPHRASE)
    config = CoreConfig.in_directory(tmp_path / "restored-before", require_auth=True)
    destination = CoreStore(config.database_path)
    destination.initialize_vault()
    restore_export(archive, config.database_path, PASSPHRASE)
    destination.purge(
        "record", record_id, confirmation=destination.purge_confirmation_phrase("record", record_id)
    )
    assert_no_plaintext(destination, [statement])
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        owner = client.post("/v1/setup", json={"name": "Synthetic restored owner"}).json()
        recovered = Fixture(
            cold, client, {"Authorization": "Bearer " + owner["token"]}, f.anchors, {}
        )
        identity = client.post(
            "/v1/admin/project-identities",
            headers=recovered.owner,
            json={"anchor_record_id": f.anchors["quarry"]},
        )
        assert identity.status_code == 200, identity.text
        recovered.projects["quarry"] = identity.json()["canonical_project_id"]
        writer, _ = recovered.principal("quarry", ["context:capture"])
        assert (
            capture(client, writer, turn(statement, observed_at=at))["reason"]
            == "capture_history_purged"
        )
        assert_no_plaintext(cold.store, [statement])


@pytest.mark.parametrize(
    "tamper", ["missing_purge", "foreign_record", "unknown_facet", "extra_content", "naive_time"]
)
def test_forged_portable_fence_fails_before_destination_mutation(
    fixture: Fixture, tmp_path: Path, tamper: str
) -> None:
    prepare(fixture, "preference")
    archive = tmp_path / "fence.atcexp"
    create_export(fixture.core.store.database_path, archive, PASSPHRASE)

    def mutate(members: dict[str, bytes], manifest: dict[str, object]) -> None:
        name = f"tables/{TABLE}.jsonl"
        row = json.loads(members[name])
        if tamper == "missing_purge":
            row["purged_at"] = "2025-01-01T00:00:00Z"
        elif tamper == "foreign_record":
            row["record_id"] = fixture.anchors["cedar"]
        elif tamper == "unknown_facet":
            row["facet_id"] = fixture.anchors["cedar"]
        elif tamper == "extra_content":
            row["forgotten_value"] = "deleted personal content"
        else:
            row["purged_at"] = "2026-10-11T00:00:00"
        members[name] = (json.dumps(row) + "\n").encode()

    forged = _rewrite_export(archive, tmp_path / "forged.atcexp", mutate)
    destination = CoreStore(tmp_path / "untouched.sqlite3")
    destination.initialize_vault()
    before = destination.database_path.read_bytes()
    with pytest.raises(ValueError):
        restore_export(forged, destination.database_path, PASSPHRASE)
    assert destination.database_path.read_bytes() == before


def test_repeated_migration_repairs_missing_additive_tables_and_backfills_intact_capture(
    fixture: Fixture,
) -> None:
    f = fixture
    writer, _ = f.principal("borealis", ["context:capture"])
    receipt = capture(f.client, writer, turn("I prefer quick responses."))
    record_id = f.core.store.get_observation(receipt["observation_id"]).record_id
    assert record_id is not None
    with f.core.store.transaction() as connection:
        connection.execute("DROP TABLE client_capture_erasure_links")
        connection.execute(f"DROP TABLE {TABLE}")
    assert f.core.store.migrate() == 22
    assert f.core.store.migrate() == 22
    with f.core.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM client_capture_erasure_links WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 1
        )
