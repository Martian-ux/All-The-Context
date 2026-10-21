"""Synthetic durable erasure checks for the raw/formed lifecycle event pair."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.storage import CoreStore, NotFoundError
from fastapi.testclient import TestClient

from tests.integration.test_bound_named_project_decisions import named_project
from tests.integration.test_decision_partial_runtime import capture, turn
from tests.integration.test_project_principal_bindings import Fixture, fixture

__all__ = ["fixture"]


def assert_no_plaintext(store: CoreStore, statements: list[str]) -> None:
    with store.connect() as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            for row in connection.execute("SELECT * FROM " + quoted):
                assert all(text not in repr(tuple(row)) for text in statements), table
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("kind", ["preference", "decision"])
@pytest.mark.parametrize("compaction", ["completed", "interrupted"])
def test_capture_history_erasure_survives_cold_rebuild_and_exact_replay(
    fixture: Fixture, kind: str, compaction: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = fixture
    project = named_project(f, "Quarry", "purge-quarry")
    writers = [f.principal(project, ["context:capture"])[0] for _ in range(3)]
    statements = (
        [
            "I prefer quick responses.",
            "Correction: I prefer thorough responses instead of quick responses.",
            "I prefer thorough responses.",
        ]
        if kind == "preference"
        else [
            "For Quarry, use DuckDB for offline cache.",
            "Correction: For Quarry, use SQLite for offline cache instead of DuckDB.",
            "For Quarry, use SQLite for offline cache.",
        ]
    )
    start = datetime.now(UTC) - timedelta(hours=2)
    events = [
        turn(text, observed_at=start + timedelta(minutes=index))
        for index, text in enumerate(statements)
    ]
    receipts = [
        capture(f.client, writer, event) for writer, event in zip(writers, events, strict=True)
    ]
    store = f.core.store
    record_id = store.get_observation(receipts[0]["observation_id"]).record_id
    assert record_id is not None
    assert all(store.get_observation(r["observation_id"]).record_id == record_id for r in receipts)
    capture_ids = [r["capture_event_id"] for r in receipts]
    fingerprints: list[str] = []
    with store.connect() as connection:
        for event_id in capture_ids:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM context_candidates WHERE capture_event_id=?", (event_id,)
                ).fetchone()[0]
                == 2
            )
            fingerprints.append(
                connection.execute(
                    "SELECT payload_hash FROM capture_events WHERE id=?", (event_id,)
                ).fetchone()[0]
            )
    job = store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    assert_no_plaintext(store, [*statements, *fingerprints])
    if compaction == "interrupted":
        with monkeypatch.context() as patch:
            patch.setattr(
                "allthecontext.storage.shutil.disk_usage",
                lambda _path: SimpleNamespace(total=1, used=1, free=0),
            )
            assert store.resume_purge_jobs(job_id=job["id"], limit=1) == 0
            assert store.get_purge_job(job["id"])["phase"] == "compaction_pending"
    else:
        assert store.resume_purge_jobs(job_id=job["id"], limit=1) == 1
    config = f.core.config
    f.client.close()
    f.core.close()
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        reopened = Fixture(cold, client, f.owner, f.anchors, f.projects)
        reader, _ = reopened.principal(project, ["context:read"])
        assert cold.store.get_purge_job(job["id"])["phase"] == "completed"
        cold.store.rebuild_integrity_groups()
        assert_no_plaintext(cold.store, [*statements, *fingerprints])
        assert record_id not in {
            i["id"] for i in reopened.bootstrap(reader, "offline cache")["items"]
        }
        assert client.get("/v1/context/" + record_id, headers=reader).status_code == 404
        assert cold.store.record_history(record_id) == []
        with pytest.raises(NotFoundError):
            cold.store.get_record(record_id, include_deleted=True)
        for writer, event, prior in zip(writers, events, receipts, strict=True):
            replay = capture(client, writer, event)
            assert replay["status"] == "replayed"
            assert replay["capture_event_id"] == prior["capture_event_id"]
            assert replay["observation_id"] == prior["observation_id"]
            assert replay.get("project_binding") == prior.get("project_binding")
            assert replay["reason"] == "capture_content_purged"
            with pytest.raises(NotFoundError):
                cold.store.get_observation(prior["observation_id"])
            # Erasure forgets payload fingerprints. Same-identity retries,
            # including altered bodies, consume no influence; identity changes
            # remain conflicts and cannot become a new event.
            conflict = client.post(
                "/v1/lifecycle/events",
                headers=writer,
                json={**event, "content": "I prefer paragraph answers."},
            )
            assert conflict.status_code == 200, conflict.text
            assert conflict.json()["observation_id"] == prior["observation_id"]
            assert conflict.json()["reason"] == "capture_content_purged"
            changed_identity = client.post(
                "/v1/lifecycle/events",
                headers=writer,
                json={**event, "sequence": event["sequence"] + 1},
            )
            assert changed_identity.status_code == 422, changed_identity.text
            assert changed_identity.json()["error"]["code"] == "capture_event_payload_conflict"
        assert_no_plaintext(cold.store, [*statements, *fingerprints])
        with cold.store.connect() as connection:
            for event_id in capture_ids:
                assert (
                    connection.execute(
                        "SELECT 1 FROM context_candidates WHERE capture_event_id=?", (event_id,)
                    ).fetchone()
                    is None
                )
                row = connection.execute(
                    "SELECT normalized_payload_json,payload_hash,status,application_receipt "
                    "FROM capture_events WHERE id=?",
                    (event_id,),
                ).fetchone()
                payload = json.loads(row["normalized_payload_json"])
                assert (
                    payload["purged_capture_receipt"]["observation_id"]
                    == receipts[capture_ids.index(event_id)]["observation_id"]
                )
                assert set(payload) == {"purged_capture_receipt", "project_binding"}
                assert row["payload_hash"] == "0" * 64
                assert row["status"] == "applied"
                assert row["application_receipt"].startswith("capture-observation-")
    for path in (
        config.database_path,
        config.database_path.with_name(config.database_path.name + "-wal"),
    ):
        if path.exists():
            assert all(
                text.encode() not in path.read_bytes() for text in [*statements, *fingerprints]
            )


def test_erasure_is_event_bound_and_preserves_identical_other_project_content(
    fixture: Fixture,
) -> None:
    f = fixture
    writers = [f.principal(key, ["context:capture"])[0] for key in ("borealis", "cedar")]
    text = "I prefer quick responses."
    receipts = [capture(f.client, writer, turn(text)) for writer in writers]
    store = f.core.store
    records = [store.get_observation(r["observation_id"]).record_id for r in receipts]
    erased, retained = records
    assert erased is not None and retained is not None and erased != retained
    store.purge("record", erased, confirmation=store.purge_confirmation_phrase("record", erased))
    assert store.get_record(retained).content == text
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM context_candidates WHERE capture_event_id=?",
                (receipts[0]["capture_event_id"],),
            ).fetchone()
            is None
        )
        rows = connection.execute(
            "SELECT content FROM context_candidates WHERE capture_event_id=?",
            (receipts[1]["capture_event_id"],),
        ).fetchall()
        assert len(rows) == 2 and all(row["content"] == text for row in rows)
        payload = connection.execute(
            "SELECT normalized_payload_json FROM capture_events WHERE id=?",
            (receipts[1]["capture_event_id"],),
        ).fetchone()[0]
        assert json.loads(payload)["content"] == text


def test_missing_observation_without_purge_receipt_still_fails_closed(fixture: Fixture) -> None:
    writer, _ = fixture.principal("borealis", ["context:capture"])
    event = turn("An unformed synthetic user turn.")
    receipt = capture(fixture.client, writer, event)
    with fixture.core.store.transaction() as connection:
        connection.execute(
            "DELETE FROM context_candidates WHERE id=?", (receipt["observation_id"],)
        )
    replay = fixture.client.post("/v1/lifecycle/events", headers=writer, json=event)
    assert replay.status_code == 409, replay.text
