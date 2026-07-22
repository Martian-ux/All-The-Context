from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from allthecontext.export import create_export, restore_export
from allthecontext.models import CandidateInput, IngestionMode
from allthecontext.replication import (
    EventType,
    ReplicationError,
    ReplicationEvent,
    build_event,
    canonical_json,
    sign_event,
    verify_event,
)
from allthecontext.storage import CoreStore, InvalidStateError, NotFoundError


def _store(path: Path) -> CoreStore:
    store = CoreStore(path)
    store.initialize_vault()
    return store


def _approve(store: CoreStore, content: str, **kwargs: object) -> str:
    candidate = store.add_candidate(CandidateInput(content=content, kind="fact", **kwargs))
    return store.approve_candidate(candidate.id).id


def test_purge_event_contract_is_opaque_signed_and_not_downgradeable() -> None:
    event = build_event(
        vault_id="vault-1",
        sequence=7,
        event_type="record_purged",
        record_id="record-1",
        payload={
            "record_id": "record-1",
            "purged_at": "2026-07-21T12:00:00+00:00",
            "purge_scope": "record",
            "irreversible": True,
        },
    )
    signed = sign_event(event, b"x" * 32)
    verify_event(signed, b"x" * 32)
    assert "content" not in canonical_json(signed.wire_mapping())

    with pytest.raises(ReplicationError, match="unexpected fields"):
        build_event(
            vault_id="vault-1",
            sequence=8,
            event_type="record_purged",
            record_id="record-1",
            payload={**dict(event.payload), "content": "must never appear"},
        )
    with pytest.raises(ReplicationError, match="must be irreversible"):
        build_event(
            vault_id="vault-1",
            sequence=8,
            event_type="record_purged",
            record_id="record-1",
            payload={**dict(event.payload), "irreversible": False},
        )


def test_schema_upgrade_adds_optional_slot_and_purge_contracts(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    migrations = (
        Path(__file__).parents[2]
        / "packages"
        / "allthecontext"
        / "src"
        / "allthecontext"
        / "migrations"
        / "core"
    )
    with sqlite3.connect(database) as connection:
        connection.executescript((migrations / "001_initial.sql").read_text(encoding="utf-8"))
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES(1,'001_initial.sql','2026-01-01T00:00:00+00:00')"
        )
        connection.executescript(
            (migrations / "002_edge_proposal_receipts.sql").read_text(encoding="utf-8")
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES"
            "(2,'002_edge_proposal_receipts.sql','2026-01-01T00:00:00+00:00')"
        )
    store = CoreStore(database)
    assert store.migrate() == 4
    with store.connect() as connection:
        candidate_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(context_candidates)")
        }
        assert {"entity_key", "attribute_key"} <= candidate_columns
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purge_jobs'"
        ).fetchone()
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_edge_clients'"
        ).fetchone()


def test_slot_metadata_stays_proposed_until_approval_and_groups_are_deterministic(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "core.sqlite3")
    first_candidate = store.add_candidate(
        CandidateInput(
            kind="preference",
            content="Blue",
            entity_key="  USER:Noah ",
            attribute_key="Favorite Color",
            evidence="explicit statement",
        )
    )
    assert first_candidate.entity_key == "user:noah"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 0

    first = store.approve_candidate(first_candidate.id)
    second_id = _approve(
        store,
        "blue!",
        entity_key="user:noah",
        attribute_key="favorite color",
    )
    third_id = _approve(
        store,
        "Green",
        entity_key="USER:NOAH",
        attribute_key="FAVORITE COLOR",
    )
    groups = store.list_integrity_groups()
    assert groups["total"] == 2
    by_type = {item["group_type"]: item for item in groups["items"]}
    assert by_type["duplicate"]["record_ids"] == sorted([first.id, second_id])
    assert by_type["conflict"]["record_ids"] == sorted([first.id, second_id, third_id])

    store.correct_record(third_id, content="BLUE", reason="user correction")
    groups = store.list_integrity_groups()
    assert groups["total"] == 1
    assert groups["items"][0]["group_type"] == "duplicate"
    assert groups["items"][0]["record_ids"] == sorted([first.id, second_id, third_id])

    store.delete_record(second_id, reason="ordinary reversible deletion")
    groups = store.list_integrity_groups()
    assert groups["items"][0]["record_ids"] == sorted([first.id, third_id])


def test_supersession_removes_old_record_from_integrity_group(tmp_path: Path) -> None:
    store = _store(tmp_path / "core.sqlite3")
    old_id = _approve(store, "Old", entity_key="person:1", attribute_key="city")
    peer_id = _approve(store, "Other", entity_key="person:1", attribute_key="city")
    assert store.list_integrity_groups()["items"][0]["record_ids"] == sorted([old_id, peer_id])

    replacement = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="Replacement",
            entity_key="person:1",
            attribute_key="city",
            supersedes=old_id,
        )
    )
    replacement_id = store.approve_candidate(replacement.id).id
    members = store.list_integrity_groups()["items"][0]["record_ids"]
    assert old_id not in members
    assert members == sorted([peer_id, replacement_id])
    store.purge(
        "record",
        old_id,
        confirmation=store.purge_confirmation_phrase("record", old_id),
        compact=False,
    )
    assert store.get_record(replacement_id).supersedes is None


def test_delete_preserves_history_but_purge_scrubs_all_core_content(tmp_path: Path) -> None:
    database = tmp_path / "core.sqlite3"
    store = _store(database)
    secret = "low entropy secret phrase"
    source = store.add_source(
        f"archive includes {secret}".encode(),
        source_service="test",
        source_type="text",
        filename="secret.txt",
        metadata={"label": secret},
    )
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
    )
    batch = store.submit_batch(
        session["session_id"],
        "batch-1",
        [
            CandidateInput(
                kind="fact",
                content=secret,
                evidence=f"evidence {secret}",
                source_id=source.id,
                availability="always_available",
                entity_key="person:1",
                attribute_key="secret",
            )
        ],
    )
    candidate_id = batch["candidate_ids"][0]
    record = store.approve_candidate(candidate_id)
    store.correct_record(record.id, content=f"corrected {secret}", reason=secret)
    deleted = store.delete_record(record.id, reason=secret)
    assert deleted["record_id"] == record.id
    assert secret in store.get_record(record.id, include_deleted=True).content
    assert any(secret in json.dumps(item) for item in store.record_history(record.id))

    with pytest.raises(InvalidStateError):
        store.purge(
            "record",
            record.id,
            confirmation=f"purge record {record.id}",
            compact=False,
        )
    phrase = store.purge_confirmation_phrase("record", record.id)
    job = store.purge("record", record.id, confirmation=phrase, compact=False)
    assert job["phase"] == "compaction_pending"
    with pytest.raises(NotFoundError):
        store.get_record(record.id, include_deleted=True)
    assert store.record_history(record.id) == []

    replayed = store.purge("record", record.id, confirmation=phrase, compact=False)
    assert replayed["id"] == job["id"]
    assert store.resume_purge_jobs(job_id=job["id"], limit=1) == 1
    assert store.get_purge_job(job["id"])["phase"] == "completed"

    with store.connect() as connection:
        events = connection.execute(
            "SELECT * FROM replication_events WHERE record_id=? ORDER BY sequence", (record.id,)
        ).fetchall()
        assert [int(item["sequence"]) for item in events] == list(range(1, len(events) + 1))
        assert str(events[-1]["event_type"]) == EventType.RECORD_PURGED.value
        final_event = ReplicationEvent.from_mapping(dict(events[-1]))
        assert final_event.payload == {
            "record_id": record.id,
            "purged_at": final_event.payload["purged_at"],
            "purge_scope": "record",
            "irreversible": True,
        }
        for event in events:
            assert secret not in str(event["payload_json"])
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            if table.startswith("context_fts_"):
                continue
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                assert secret not in repr(tuple(row))
        tombstone = connection.execute(
            "SELECT * FROM purge_tombstones WHERE stable_id=?", (record.id,)
        ).fetchone()
        assert tombstone is not None
        assert "hash" not in set(map(str.casefold, tombstone.keys()))
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
    for path in (database, database.with_name(f"{database.name}-wal")):
        if path.exists():
            assert secret.encode() not in path.read_bytes()


def test_shared_source_is_retained_until_last_dependent_record_is_purged(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "core.sqlite3")
    source = store.add_source(b"shared source", source_service="test", source_type="text")
    first = _approve(store, "first", source_id=source.id)
    second = _approve(store, "second", source_id=source.id)

    store.purge(
        "record",
        first,
        confirmation=store.purge_confirmation_phrase("record", first),
        compact=False,
    )
    assert store.get_source_content(source.id) == b"shared source"
    store.purge(
        "record",
        second,
        confirmation=store.purge_confirmation_phrase("record", second),
        compact=False,
    )
    with pytest.raises(NotFoundError):
        store.get_source_content(source.id)


def test_source_purge_removes_all_attributable_records(tmp_path: Path) -> None:
    store = _store(tmp_path / "core.sqlite3")
    source = store.add_source(b"source secret", source_service="test", source_type="text")
    record_ids = [_approve(store, value, source_id=source.id) for value in ("one", "two")]
    job = store.purge(
        "source",
        source.id,
        confirmation=store.purge_confirmation_phrase("source", source.id),
        compact=False,
    )
    assert job["target_type"] == "source"
    for record_id in record_ids:
        with pytest.raises(NotFoundError):
            store.get_record(record_id, include_deleted=True)


def test_pending_compaction_is_restart_resumable(tmp_path: Path) -> None:
    database = tmp_path / "core.sqlite3"
    store = _store(database)
    record_id = _approve(store, "restart secret")
    job = store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )

    restarted = CoreStore(database)
    restarted.migrate()
    assert restarted.resume_purge_jobs(limit=1) == 1
    assert restarted.get_purge_job(job["id"])["phase"] == "completed"


def test_compaction_fails_closed_on_lock_and_resumes(tmp_path: Path) -> None:
    database = tmp_path / "core.sqlite3"
    store = _store(database)
    record_id = _approve(store, "locked secret")
    job = store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    writer = sqlite3.connect(database, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        assert store.resume_purge_jobs(job_id=job["id"], limit=1) == 0
        pending = store.get_purge_job(job["id"])
        assert pending["phase"] == "compaction_pending"
        assert pending["last_error_code"] in {None, "database_locked"}
    finally:
        writer.rollback()
        writer.close()
    assert store.resume_purge_jobs(job_id=job["id"], limit=1) == 1


def test_compaction_fails_closed_when_disk_preflight_is_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "core.sqlite3"
    store = _store(database)
    record_id = _approve(store, "disk secret")
    job = store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    monkeypatch.setattr(
        "allthecontext.storage.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=1, used=1, free=0),
    )
    assert store.resume_purge_jobs(job_id=job["id"], limit=1) == 0
    pending = store.get_purge_job(job["id"])
    assert pending["phase"] == "compaction_pending"
    assert pending["last_error_code"] == "insufficient_disk"


def test_restore_cannot_resurrect_a_pre_purge_record_or_source(tmp_path: Path) -> None:
    database = tmp_path / "core.sqlite3"
    export_path = tmp_path / "before.atcexp"
    store = _store(database)
    source = store.add_source(b"restore secret", source_service="test", source_type="text")
    record_id = _approve(store, "restore secret", source_id=source.id)
    create_export(
        database,
        export_path,
        "correct horse battery staple",
        include_sources=True,
        include_audit=True,
    )
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
    )

    restore_export(export_path, database, "correct horse battery staple")
    with pytest.raises(NotFoundError):
        store.get_record(record_id, include_deleted=True)
    with pytest.raises(NotFoundError):
        store.get_source_content(source.id)
