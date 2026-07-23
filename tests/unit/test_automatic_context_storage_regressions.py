from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext.core.service import CoreService
from allthecontext.export import create_export, restore_export
from allthecontext.models import CandidateInput, SearchRequest
from allthecontext.storage import CoreStore, NotFoundError

PASSPHRASE = "correct horse battery staple"


def _migration_directory() -> Path:
    return (
        Path(__file__).parents[2]
        / "packages"
        / "allthecontext"
        / "src"
        / "allthecontext"
        / "migrations"
        / "core"
    )


def _create_v4_database(path: Path, *, seed_legacy_rows: bool = False) -> None:
    migrations = _migration_directory()
    with sqlite3.connect(path) as connection:
        connection.executescript((migrations / "001_initial.sql").read_text(encoding="utf-8"))
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES(1,'001_initial.sql','2026-01-01T00:00:00+00:00')"
        )
        for version, filename in (
            (2, "002_edge_proposal_receipts.sql"),
            (3, "003_memory_integrity_purge.sql"),
            (4, "004_remote_edge_clients.sql"),
        ):
            connection.executescript((migrations / filename).read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations VALUES(?,?,?)",
                (version, filename, "2026-01-01T00:00:00+00:00"),
            )
        connection.execute(
            "INSERT INTO vaults(id,name,display_timezone,created_at,schema_version) "
            "VALUES('legacy-vault','Legacy','UTC','2026-01-01T00:00:00+00:00',4)"
        )
        if not seed_legacy_rows:
            return

        common_candidate_values = (
            "legacy-vault",
            "fact",
            "[]",
            "[]",
            1.0,
            "normal",
            "core_only",
            "[]",
            "[]",
            1,
            1,
            "2026-01-02T00:00:00+00:00",
        )
        connection.execute(
            "INSERT INTO context_candidates("
            "id,vault_id,kind,content,scopes_json,tags_json,confidence,sensitivity,"
            "availability,allowed_clients_json,denied_clients_json,"
            "explicit_user_statement,approval_status,content_hash,schema_version,"
            "created_at,reviewed_at,reviewed_by,review_reason"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-approved",
                *common_candidate_values[:2],
                "Legacy approved context",
                *common_candidate_values[2:10],
                "approved",
                "approved-hash",
                *common_candidate_values[10:],
                "2026-01-03T00:00:00+00:00",
                "legacy-user",
                "kept before automatic policy",
            ),
        )
        connection.execute(
            "INSERT INTO context_candidates("
            "id,vault_id,kind,content,scopes_json,tags_json,confidence,sensitivity,"
            "availability,allowed_clients_json,denied_clients_json,"
            "explicit_user_statement,approval_status,content_hash,schema_version,"
            "created_at,reviewed_at,reviewed_by,review_reason"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-rejected",
                *common_candidate_values[:2],
                "Legacy rejected context",
                *common_candidate_values[2:10],
                "rejected",
                "rejected-hash",
                *common_candidate_values[10:],
                "2026-01-03T00:00:00+00:00",
                "legacy-user",
                "discarded before automatic policy",
            ),
        )
        connection.execute(
            "INSERT INTO context_records("
            "id,vault_id,candidate_id,kind,content,scopes_json,tags_json,confidence,"
            "sensitivity,availability,allowed_clients_json,denied_clients_json,"
            "explicit_user_statement,approval_status,version,content_hash,schema_version,"
            "created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-record",
                "legacy-vault",
                "legacy-approved",
                "fact",
                "Legacy approved context",
                "[]",
                "[]",
                1.0,
                "normal",
                "core_only",
                "[]",
                "[]",
                1,
                "approved",
                1,
                "approved-hash",
                1,
                "2026-01-03T00:00:00+00:00",
                "2026-01-03T00:00:00+00:00",
            ),
        )


def _store(path: Path) -> CoreStore:
    store = CoreStore(path)
    store.initialize_vault()
    return store


def _approve(store: CoreStore, content: str, **kwargs: object) -> str:
    candidate = store.add_candidate(CandidateInput(kind="fact", content=content, **kwargs))
    return store.approve_candidate(candidate.id).id


def test_migration_005_recovers_after_partial_application_and_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "partial-v5.sqlite3"
    _create_v4_database(database, seed_legacy_rows=True)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE context_candidates ADD COLUMN observed_at TEXT")
        connection.execute("ALTER TABLE context_candidates ADD COLUMN observation_origin TEXT")
        connection.execute(
            "ALTER TABLE context_candidates ADD COLUMN disposition TEXT NOT NULL DEFAULT 'staged'"
        )

    store = CoreStore(database)
    assert store.migrate() == 6
    assert store.migrate() == 6

    with store.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=5").fetchone()[
                0
            ]
            == 1
        )
        candidate_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(context_candidates)")
        }
        assert {
            "observed_at",
            "observation_origin",
            "disposition",
            "record_id",
            "decision_reason",
            "decided_at",
            "policy_version",
        } <= candidate_columns
        rows = connection.execute(
            "SELECT id,disposition FROM context_candidates ORDER BY id"
        ).fetchall()
        assert [(str(row["id"]), str(row["disposition"])) for row in rows] == [
            ("legacy-approved", "applied"),
            ("legacy-rejected", "ignored"),
        ]


def test_delete_history_is_contiguous_and_historical_restore_recovers_provenance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "history.sqlite3")
    predecessor_a = _approve(store, "Predecessor A")
    predecessor_b = _approve(store, "Predecessor B")
    source_a = store.add_source(
        b"source a",
        source_service="test",
        source_type="text",
        filename="a.txt",
    )
    source_b = store.add_source(
        b"source b",
        source_service="test",
        source_type="text",
        filename="b.txt",
    )
    original = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="Original sourced value",
            source_id=source_a.id,
            source_reference="archive/a#1",
            supersedes=predecessor_a,
        )
    )
    record_id = store.approve_candidate(original.id).id

    with store.transaction() as connection:
        connection.execute(
            "UPDATE context_records SET source_id=?,source_reference=?,supersedes=? WHERE id=?",
            (source_b.id, "archive/b#2", predecessor_b, record_id),
        )
    store.correct_record(record_id, content="Changed sourced value", reason="later correction")
    deletion = store.delete_record(record_id, reason="temporary deletion")

    history = store.record_history(record_id)
    assert deletion["deleted_version"] == 3
    assert [item["version"] for item in history] == [1, 2, 3]
    assert history[2]["reason"] == "temporary deletion"

    restored = store.restore_record(record_id, version=1, reason="restore original provenance")
    assert restored.version == 4
    assert restored.content == "Original sourced value"
    assert restored.source_id == source_a.id
    assert restored.source_reference == "archive/a#1"
    assert restored.supersedes == predecessor_a
    assert [item["version"] for item in store.record_history(record_id)] == [1, 2, 3, 4]


def test_source_free_export_restore_has_valid_foreign_keys_and_rebuilt_search(
    tmp_path: Path,
) -> None:
    source_database = tmp_path / "source.sqlite3"
    source_store = _store(source_database)
    source = source_store.add_source(
        b"portable source body",
        source_service="test",
        source_type="text",
    )
    record_id = _approve(
        source_store,
        "Portable zephyr retrieval marker",
        source_id=source.id,
        source_reference="source#marker",
    )
    package = tmp_path / "source-free.atcexp"
    create_export(
        source_database,
        package,
        PASSPHRASE,
        include_sources=False,
        include_audit=True,
    )

    destination = CoreService.in_directory(tmp_path / "restored")
    restore_export(package, destination.config.database_path, PASSPHRASE)

    with destination.store.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute(
                "SELECT source_id FROM context_records WHERE id=?", (record_id,)
            ).fetchone()["source_id"]
            is None
        )
        assert (
            connection.execute(
                "SELECT source_id FROM context_candidates "
                "WHERE id=(SELECT candidate_id FROM context_records WHERE id=?)",
                (record_id,),
            ).fetchone()["source_id"]
            is None
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_fts WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 1
        )
    result = destination.retrieval.search(SearchRequest(query="zephyr marker"))
    assert [item.id for item in result.items] == [record_id]


def test_pre_v5_decisions_restore_without_reopening_rejected_observations(
    tmp_path: Path,
) -> None:
    legacy_database = tmp_path / "legacy-v4.sqlite3"
    _create_v4_database(legacy_database, seed_legacy_rows=True)
    package = tmp_path / "legacy-v4.atcexp"
    manifest = create_export(legacy_database, package, PASSPHRASE)
    assert manifest["schema_version"] == 4

    destination_dir = tmp_path / "destination"
    destination = CoreService.in_directory(destination_dir)
    restore_export(package, destination.config.database_path, PASSPHRASE)
    restore_export(package, destination.config.database_path, PASSPHRASE)

    restarted = CoreService.in_directory(destination_dir)
    assert restarted.store.evaluate_staged_observations() == 0
    with restarted.store.connect() as connection:
        rows = connection.execute(
            "SELECT id,approval_status,disposition,record_id,observation_origin,"
            "policy_version FROM context_candidates "
            "WHERE id IN ('legacy-approved','legacy-rejected') ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                "legacy-approved",
                "approved",
                "applied",
                "legacy-record",
                "legacy_migration",
                "legacy-review-v1",
            ),
            (
                "legacy-rejected",
                "rejected",
                "ignored",
                None,
                "legacy_migration",
                "legacy-review-v1",
            ),
        ]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_records WHERE id='legacy-record'"
            ).fetchone()[0]
            == 1
        )


def test_purge_then_restore_cannot_resurrect_unlinked_target_observations(
    tmp_path: Path,
) -> None:
    database = tmp_path / "purge.sqlite3"
    store = _store(database)
    record_id = _approve(store, "Canonical purge target")
    error_observation = store.add_context_error_observation(
        CandidateInput(
            kind="context_error",
            content="Raw error-only observation marker",
        ),
        record_id=record_id,
        description="The target needs correction",
        evidence=None,
    )
    superseding_observation = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="Raw supersedes-only observation marker",
            supersedes=record_id,
        )
    )
    mapped_observation = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="Raw record-id-only observation marker",
        )
    )
    observation_ids = {
        error_observation.id,
        superseding_observation.id,
        mapped_observation.id,
    }
    with store.transaction() as connection:
        connection.execute(
            "UPDATE context_candidates SET record_id=? WHERE id=?",
            (record_id, mapped_observation.id),
        )
        connection.execute(
            "DELETE FROM context_observation_links "
            f"WHERE observation_id IN ({','.join('?' for _ in observation_ids)})",
            sorted(observation_ids),
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_observation_links "
                f"WHERE observation_id IN ({','.join('?' for _ in observation_ids)})",
                sorted(observation_ids),
            ).fetchone()[0]
            == 0
        )

    package = tmp_path / "before-purge.atcexp"
    create_export(database, package, PASSPHRASE, include_audit=True)
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )

    restore_export(package, database, PASSPHRASE)
    with pytest.raises(NotFoundError):
        store.get_record(record_id, include_deleted=True)
    with store.connect() as connection:
        placeholders = ",".join("?" for _ in observation_ids)
        assert (
            connection.execute(
                f"SELECT COUNT(*) FROM context_candidates WHERE id IN ({placeholders})",
                sorted(observation_ids),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_errors WHERE record_id=? OR candidate_id IN "
                f"({placeholders})",
                (record_id, *sorted(observation_ids)),
            ).fetchone()[0]
            == 0
        )
