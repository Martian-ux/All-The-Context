from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.export import create_export, restore_export
from allthecontext.models import (
    ApprovalRequest,
    Availability,
    CandidateInput,
    CoverageReport,
    IngestionMode,
    MemoryTruthStatus,
    ObservationDisposition,
    SearchRequest,
)
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import (
    SOURCE_REBUILD_REASON,
    CoreStore,
    InvalidStateError,
    NotFoundError,
    source_rebuild_marker,
)
from fastapi.testclient import TestClient


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.sqlite3")
    store.initialize_vault()
    return store


def _archive_observation(
    store: CoreStore,
    source_id: str,
    *,
    content: str,
    source_reference: str,
    session_key: str,
    entity_key: str | None = None,
    attribute_key: str | None = None,
) -> str:
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=f"session-{session_key}",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        f"batch-{session_key}",
        [
            CandidateInput(
                kind="preference",
                content=content,
                source_id=source_id,
                source_reference=source_reference,
                source_service="fiction-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
                entity_key=entity_key,
                attribute_key=attribute_key,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fiction-archive"], complete=True),
    )
    return str(batch["candidate_ids"][0])


def _publish_rebuild(
    store: CoreStore,
    source_id: str,
    *,
    content: str,
    source_reference: str,
    session_key: str,
    generation: int = 1,
) -> tuple[list[str], str]:
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=f"archive:{source_id}:fixture:rebuild:{generation}",
    )
    session_id = str(session["session_id"])
    batch = store.submit_batch(
        session_id,
        f"batch-{session_key}",
        [
            CandidateInput(
                kind="preference",
                content=content,
                source_id=source_id,
                source_reference=source_reference,
                source_service="fiction-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        session_id,
        CoverageReport(available=["fiction-archive"], complete=True),
        publish=False,
    )
    marker = source_rebuild_marker(source_id, store.get_source(source_id).content_hash, generation)
    store.update_source_import(
        source_id,
        import_status="processing",
        metadata={
            "rebuild_generation": generation,
            "rebuild_in_progress": True,
            "rebuild_source_marker": marker,
        },
        parser_warnings=[],
    )
    return store.publish_source_rebuild(
        source_id,
        session_id,
        rebuild_generation=generation,
    ), str(batch["candidate_ids"][0])


def test_direct_correction_cannot_create_self_supersession_cycle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    observation_id = _archive_observation(
        store,
        source.id,
        content="The primary fixture remains current.",
        source_reference="message:self-cycle",
        session_key="self-cycle",
        entity_key="fixture",
        attribute_key="primary",
    )
    record_id = store.get_observation(observation_id).record_id
    assert record_id is not None

    with pytest.raises(InvalidStateError, match="create a cycle"):
        store.correct_record(
            record_id,
            content="The primary fixture remains corrected.",
            reason="adversarial self-cycle probe",
            supersedes=record_id,
        )

    assert store.get_record(record_id).supersedes is None
    assert RetrievalEngine(store).search(SearchRequest(query="primary fixture")).total == 1


def test_manual_approval_cannot_publish_a_dangling_supersession_target(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="The dangling fixture must remain provisional.",
            supersedes="missing-canonical-record",
            explicit_user_statement=False,
            idempotency_key="dangling-supersession-candidate",
        )
    )

    with pytest.raises(InvalidStateError, match="target does not exist"):
        store.approve_candidate(candidate.id, ApprovalRequest(), actor="test")

    assert store.get_candidate(candidate.id).approval_status.value == "pending"
    assert RetrievalEngine(store).search(SearchRequest(query="dangling fixture")).total == 0


def test_direct_correction_cannot_close_a_two_record_supersession_cycle(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    first_observation = _archive_observation(
        store,
        source.id,
        content="The alpha fixture is current.",
        source_reference="message:alpha-cycle",
        session_key="alpha-cycle",
        entity_key="fixture",
        attribute_key="alpha",
    )
    second_observation = _archive_observation(
        store,
        source.id,
        content="The beta fixture is current.",
        source_reference="message:beta-cycle",
        session_key="beta-cycle",
        entity_key="fixture",
        attribute_key="beta",
    )
    first_id = store.get_observation(first_observation).record_id
    second_id = store.get_observation(second_observation).record_id
    assert first_id is not None and second_id is not None

    store.correct_record(
        first_id,
        content="The alpha fixture supersedes beta.",
        reason="adversarial acyclic setup",
        supersedes=second_id,
    )
    with pytest.raises(InvalidStateError, match="create a cycle"):
        store.correct_record(
            second_id,
            content="The beta fixture must not supersede alpha.",
            reason="adversarial two-node cycle probe",
            supersedes=first_id,
        )

    assert store.get_record(second_id).supersedes is None
    RetrievalEngine(store).search(SearchRequest(query="fixture"))


def test_public_source_withdrawal_cannot_mint_rebuild_tombstone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )

    first_observation_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="first",
    )
    first = store.get_observation(first_observation_id)
    assert first.disposition == ObservationDisposition.APPLIED
    assert first.record_id is not None
    record_id = first.record_id

    with pytest.raises(InvalidStateError, match="publish_source_rebuild"):
        store.withdraw_automatic_source_records(source.id)
    with (
        pytest.raises(InvalidStateError, match="validated publish ceremony"),
        store.transaction() as connection,
    ):
        store._delete_record_for_source_rebuild_tx(
            connection,
            record_id,
            source_id=source.id,
            actor="test",
            recompute_integrity=False,
        )
    assert store.get_record(record_id).id == record_id


def test_valid_publish_binds_rebuild_tombstone_to_session_generation_and_marker(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    first_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="valid-first",
    )
    record_id = store.get_observation(first_id).record_id
    assert record_id is not None

    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
        idempotency_key=f"archive:{source.id}:fixture-parser:rebuild:1",
    )
    session_id = str(session["session_id"])
    batch = store.submit_batch(
        session_id,
        "valid-rebuild-batch",
        [
            CandidateInput(
                kind="preference",
                content="I prefer concise answers.",
                source_id=source.id,
                source_reference="message:1",
                source_service="fiction-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        session_id,
        CoverageReport(available=["fiction-archive"], complete=True),
        publish=False,
    )
    marker = source_rebuild_marker(source.id, source.content_hash, 1)
    store.update_source_import(
        source.id,
        import_status="processing",
        metadata={
            "rebuild_generation": 1,
            "rebuild_in_progress": True,
            "rebuild_source_marker": marker,
        },
        parser_warnings=[],
    )

    assert store.publish_source_rebuild(source.id, session_id, rebuild_generation=1) == [record_id]
    replacement = store.get_observation(str(batch["candidate_ids"][0]))
    assert replacement.record_id == record_id
    with store.connect() as connection:
        tombstone = connection.execute(
            "SELECT deletion_origin,deletion_source_id,rebuild_session_id,"
            "rebuild_generation,rebuild_source_marker FROM deletion_tombstones "
            "WHERE record_id=?",
            (record_id,),
        ).fetchone()
    # The publish transaction consumed the tombstone while reapplying the
    # replacement observation, so the stable-ID history is the durable proof.
    assert tombstone is None
    assert store.record_history(record_id)


@pytest.mark.parametrize("reason", ["audited restore", SOURCE_REBUILD_REASON])
def test_user_restore_marker_blocks_later_source_rebuild_without_replacement(
    tmp_path: Path, reason: str
) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"restore boundary archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    observation_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:restore-boundary",
        session_key="restore-boundary-initial",
    )
    record_id = store.get_observation(observation_id).record_id
    assert record_id is not None

    store.delete_record(record_id, reason="ordinary deletion before restore")
    store.restore_record(record_id, reason=reason)

    with store.connect() as connection:
        mutation = connection.execute(
            "SELECT mutation_kind,mutation_origin FROM context_user_mutations "
            "WHERE record_id=? ORDER BY created_at DESC,id LIMIT 1",
            (record_id,),
        ).fetchone()
    assert mutation is not None
    assert tuple(mutation) == ("restore", "local_user")

    withdrawn, replacement_observation_id = _publish_rebuild(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:restore-boundary",
        session_key="restore-boundary-rebuild",
    )

    assert withdrawn == []
    assert store.get_observation(replacement_observation_id).record_id == record_id
    assert store.get_record(record_id).id == record_id
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_records WHERE source_id=? AND record_key IS NOT NULL",
                (source.id,),
            ).fetchone()[0]
            == 1
        )


def test_user_mutation_marker_survives_restart_export_restore_and_purge(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite3"
    store = CoreStore(database)
    store.initialize_vault()
    source = store.add_source(
        b"portable restore boundary archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    observation_id = _archive_observation(
        store,
        source.id,
        content="I prefer local backups.",
        source_reference="message:portable-boundary",
        session_key="portable-boundary-initial",
    )
    record_id = store.get_observation(observation_id).record_id
    assert record_id is not None
    pre_marker_package = tmp_path / "pre-marker.atcexp"
    create_export(
        database,
        pre_marker_package,
        "correct horse battery staple",
        include_sources=True,
    )
    store.delete_record(record_id, reason="delete before portable restore")
    store.restore_record(record_id, reason="portable restore")

    restarted = CoreStore(database)
    restarted.initialize_vault()
    with restarted.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 2
        )

    # An older backup has no ledger row and must not be able to clear the
    # destination's already-recorded user mutation.
    restore_export(pre_marker_package, database, "correct horse battery staple")
    with restarted.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT 1 FROM context_user_mutations WHERE record_id=? "
                "AND mutation_kind='restore'",
                (record_id,),
            ).fetchone()
            is not None
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations "
                "WHERE record_id=? AND mutation_kind='legacy_user_edit'",
                (record_id,),
            ).fetchone()[0]
            == 1
        )

    # Repeating the same older restore must not accumulate inferred rows or
    # create another typed user action.
    restore_export(pre_marker_package, database, "correct horse battery staple")
    with restarted.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations "
                "WHERE record_id=? AND mutation_kind IN ('delete','restore')",
                (record_id,),
            ).fetchone()[0]
            == 2
        )

    package = tmp_path / "portable-boundary.atcexp"
    create_export(database, package, "correct horse battery staple", include_sources=True)
    destination = tmp_path / "restored.sqlite3"
    restored = CoreStore(destination)
    restored.initialize_vault()
    restore_export(package, destination, "correct horse battery staple")
    with restored.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations "
                "WHERE record_id=? AND mutation_kind='legacy_user_edit'",
                (record_id,),
            ).fetchone()[0]
            == 1
        )

    purge = restored.purge(
        "record",
        record_id,
        confirmation=restored.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    assert purge["target_id"] == record_id
    with restored.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 3
        )


def test_user_mutation_ledger_is_append_only_and_legacy_upgrade_backfills_restore(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    store = CoreStore(database)
    store.initialize_vault()
    source = store.add_source(
        b"legacy restore boundary archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    observation_id = _archive_observation(
        store,
        source.id,
        content="I prefer durable history.",
        source_reference="message:legacy-boundary",
        session_key="legacy-boundary-initial",
    )
    record_id = store.get_observation(observation_id).record_id
    assert record_id is not None
    store.delete_record(record_id, reason="legacy delete")
    store.restore_record(record_id, reason="arbitrary legacy restore reason")

    with store.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM context_user_mutations WHERE record_id=?", (record_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE context_user_mutations SET actor='tampered' WHERE record_id=?",
                (record_id,),
            )

    partial_database = tmp_path / "partial-013.sqlite3"
    partial = CoreStore(partial_database)
    partial.initialize_vault()
    with sqlite3.connect(partial_database) as connection:
        connection.execute("DROP TRIGGER reject_context_user_mutations_delete")
        connection.execute("DELETE FROM schema_migrations WHERE version=13")
        vault_id = str(connection.execute("SELECT id FROM vaults LIMIT 1").fetchone()[0])
        connection.execute(
            "INSERT INTO context_user_mutations"
            "(id,vault_id,record_id,mutation_kind,mutation_origin,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "partial-marker",
                vault_id,
                "missing-record",
                "restore",
                "local_user",
                "test",
                "2026-08-22T00:00:00Z",
            ),
        )
    partial_restarted = CoreStore(partial_database)
    assert partial_restarted.migrate() == 15
    assert partial_restarted.migrate() == 15
    with (
        partial_restarted.connect() as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        connection.execute("DELETE FROM context_user_mutations WHERE record_id='missing-record'")

    # Recreate a pre-013 database boundary: the record history remains, but the
    # migration marker and new ledger table are absent when Core restarts.
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE context_user_mutations")
        connection.execute("DELETE FROM schema_migrations WHERE version=13")
    legacy_restarted = CoreStore(database)
    assert legacy_restarted.migrate() == 15
    assert legacy_restarted.migrate() == 15
    with legacy_restarted.connect() as connection:
        marker = connection.execute(
            "SELECT mutation_kind,mutation_origin,actor FROM context_user_mutations "
            "WHERE record_id=?",
            (record_id,),
        ).fetchone()
    assert marker is not None
    assert tuple(marker) == ("legacy_user_edit", "local_user", "migration-013")


@pytest.mark.parametrize(
    "missing_triggers",
    [
        ("reject_context_user_mutations_update",),
        ("reject_context_user_mutations_delete",),
        (
            "reject_context_user_mutations_update",
            "reject_context_user_mutations_delete",
        ),
    ],
)
def test_migration_013_repairs_each_append_only_trigger_when_already_applied(
    tmp_path: Path, missing_triggers: tuple[str, ...]
) -> None:
    database = tmp_path / ("trigger-repair-" + "-".join(missing_triggers) + ".sqlite3")
    store = CoreStore(database)
    store.initialize_vault()
    with sqlite3.connect(database) as connection:
        for trigger in missing_triggers:
            connection.execute(f"DROP TRIGGER {trigger}")
        vault_id = str(connection.execute("SELECT id FROM vaults LIMIT 1").fetchone()[0])
        connection.execute(
            "INSERT INTO context_user_mutations"
            "(id,vault_id,record_id,mutation_kind,mutation_origin,actor,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "trigger-repair-marker",
                vault_id,
                "missing-record",
                "restore",
                "local_user",
                "local-user",
                "2026-08-22T00:00:00Z",
            ),
        )

    restarted = CoreStore(database)
    assert restarted.migrate() == 15
    assert restarted.migrate() == 15
    with restarted.connect() as connection:
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name IN ('reject_context_user_mutations_update',"
                "'reject_context_user_mutations_delete')"
            )
        }
        assert triggers == {
            "reject_context_user_mutations_update",
            "reject_context_user_mutations_delete",
        }
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE context_user_mutations SET actor='tampered' "
                "WHERE id='trigger-repair-marker'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM context_user_mutations WHERE id='trigger-repair-marker'"
            )


def test_migration_014_fresh_and_restart_passes_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "fresh-014.sqlite3"
    store = CoreStore(database)
    assert store.migrate() == 15
    assert store.migrate() == 15

    restarted = CoreStore(database)
    assert restarted.migrate() == 15
    assert restarted.migrate() == 15
    with restarted.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute('PRAGMA table_info("context_record_versions")')
        }
        assert {"user_action_kind", "user_action_key"} <= columns
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='uq_context_record_versions_user_action'"
            ).fetchone()
            is not None
        )


@pytest.mark.parametrize(
    "missing_columns",
    [
        ("user_action_kind",),
        ("user_action_key",),
        ("user_action_kind", "user_action_key"),
    ],
    ids=["kind", "key", "both"],
)
def test_migration_014_repairs_missing_typed_action_columns_when_already_applied(
    tmp_path: Path, missing_columns: tuple[str, ...]
) -> None:
    database = tmp_path / ("typed-action-columns-" + "-".join(missing_columns) + ".sqlite3")
    store = CoreStore(database)
    store.initialize_vault()
    source = store.add_source(
        b"typed action column repair",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    observation_id = _archive_observation(
        store,
        source.id,
        content="A typed action repair fixture.",
        source_reference="message:typed-action-columns",
        session_key="typed-action-columns",
    )
    record_id = store.get_observation(observation_id).record_id
    assert record_id is not None
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT 1 FROM schema_migrations WHERE version=14").fetchone()
            is not None
        )
        connection.execute("DROP INDEX uq_context_record_versions_user_action")
        for column in missing_columns:
            connection.execute(f"ALTER TABLE context_record_versions DROP COLUMN {column}")

    restarted = CoreStore(database)
    assert restarted.migrate() == 15
    assert restarted.migrate() == 15
    with restarted.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute('PRAGMA table_info("context_record_versions")')
        }
        assert {"user_action_kind", "user_action_key"} <= columns
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='uq_context_record_versions_user_action'"
            ).fetchone()
            is not None
        )

    restarted.correct_record(record_id, content="Corrected after schema repair.", reason="test")
    with restarted.connect() as connection:
        evidence = connection.execute(
            "SELECT user_action_kind,user_action_key FROM context_record_versions "
            "WHERE record_id=? AND user_action_key IS NOT NULL",
            (record_id,),
        ).fetchall()
    assert [tuple(row) for row in evidence] == [("correction", f"correction:{record_id}:2")]


def test_migration_014_repairs_missing_index_without_changing_typed_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "typed-action-index.sqlite3"
    store = CoreStore(database)
    store.initialize_vault()
    source = store.add_source(
        b"typed action index repair",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    observation_id = _archive_observation(
        store,
        source.id,
        content="An existing typed action.",
        source_reference="message:typed-action-index",
        session_key="typed-action-index",
    )
    record_id = store.get_observation(observation_id).record_id
    assert record_id is not None
    store.correct_record(record_id, content="Existing correction.", reason="test")
    with sqlite3.connect(database) as connection:
        before = [
            tuple(row)
            for row in connection.execute(
                "SELECT user_action_kind,user_action_key FROM context_record_versions "
                "WHERE record_id=? AND user_action_key IS NOT NULL",
                (record_id,),
            ).fetchall()
        ]
        connection.execute("DROP INDEX uq_context_record_versions_user_action")

    restarted = CoreStore(database)
    assert restarted.migrate() == 15
    assert restarted.migrate() == 15
    with restarted.connect() as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT user_action_kind,user_action_key FROM context_record_versions "
                "WHERE record_id=? AND user_action_key IS NOT NULL",
                (record_id,),
            ).fetchall()
        ] == before
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='uq_context_record_versions_user_action'"
            ).fetchone()
            is not None
        )

    restarted.correct_record(record_id, content="Second correction.", reason="test")
    with restarted.connect() as connection:
        keys = [
            str(row[0])
            for row in connection.execute(
                "SELECT user_action_key FROM context_record_versions "
                "WHERE record_id=? AND user_action_key IS NOT NULL ORDER BY version",
                (record_id,),
            ).fetchall()
        ]
    assert keys == [f"correction:{record_id}:2", f"correction:{record_id}:3"]
    assert len(keys) == len(set(keys))


def test_migration_014_repair_keeps_all_typed_local_action_paths_operational(
    tmp_path: Path,
) -> None:
    database = tmp_path / "typed-action-paths.sqlite3"
    store = CoreStore(database)
    store.initialize_vault()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP INDEX uq_context_record_versions_user_action")
        connection.execute("ALTER TABLE context_record_versions DROP COLUMN user_action_kind")
        connection.execute("ALTER TABLE context_record_versions DROP COLUMN user_action_key")

    repaired = CoreStore(database)
    assert repaired.migrate() == 15
    assert repaired.migrate() == 15

    correction_source = repaired.add_source(
        b"correction action",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    correction_observation = _archive_observation(
        repaired,
        correction_source.id,
        content="Correction action fixture.",
        source_reference="message:correction-action",
        session_key="correction-action",
    )
    correction_record_id = repaired.get_observation(correction_observation).record_id
    assert correction_record_id is not None
    repaired.correct_record(correction_record_id, content="Corrected.", reason="test")

    availability_source = repaired.add_source(
        b"availability action",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    availability_observation = _archive_observation(
        repaired,
        availability_source.id,
        content="Availability action fixture.",
        source_reference="message:availability-action",
        session_key="availability-action",
    )
    availability_record_id = repaired.get_observation(availability_observation).record_id
    assert availability_record_id is not None
    repaired.change_availability(availability_record_id, Availability.LOCAL)

    restore_source = repaired.add_source(
        b"restore action",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    restore_observation = _archive_observation(
        repaired,
        restore_source.id,
        content="Restore action fixture.",
        source_reference="message:restore-action",
        session_key="restore-action",
    )
    restore_record_id = repaired.get_observation(restore_observation).record_id
    assert restore_record_id is not None
    repaired.delete_record(restore_record_id, reason="test")
    repaired.restore_record(restore_record_id, reason="test")

    source_delete_source = repaired.add_source(
        b"source delete action",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    source_delete_observation = _archive_observation(
        repaired,
        source_delete_source.id,
        content="Source delete action fixture.",
        source_reference="message:source-delete-action",
        session_key="source-delete-action",
    )
    source_delete_record_id = repaired.get_observation(source_delete_observation).record_id
    assert source_delete_record_id is not None
    repaired.delete_source(source_delete_source.id, reason="test")

    with repaired.connect() as connection:
        rows = connection.execute(
            "SELECT user_action_kind,user_action_key FROM context_record_versions "
            "WHERE user_action_key IS NOT NULL"
        ).fetchall()
    assert {str(row["user_action_kind"]) for row in rows} == {
        "correction",
        "availability_change",
        "delete",
        "restore",
        "source_delete",
    }
    keys = [str(row["user_action_key"]) for row in rows]
    assert len(keys) == len(set(keys))


def test_legacy_upgrade_keeps_trusted_rebuild_tombstone_automatic(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-rebuild.sqlite3"
    store = CoreStore(database)
    store.initialize_vault()
    source = store.add_source(
        b"legacy automatic rebuild archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    observation_id = _archive_observation(
        store,
        source.id,
        content="The original automatic value.",
        source_reference="message:legacy-automatic",
        session_key="legacy-automatic-initial",
    )
    record_id = store.get_observation(observation_id).record_id
    assert record_id is not None

    withdrawn, _ = _publish_rebuild(
        store,
        source.id,
        content="A changed automatic value.",
        source_reference="message:legacy-automatic-new",
        session_key="legacy-automatic-rebuild",
    )
    assert withdrawn == [record_id]
    with store.connect() as connection:
        tombstone = connection.execute(
            "SELECT deletion_origin FROM deletion_tombstones WHERE record_id=?",
            (record_id,),
        ).fetchone()
    assert tombstone is not None
    assert tombstone[0] == "source_rebuild"

    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TABLE context_user_mutations")
        connection.execute("DELETE FROM schema_migrations WHERE version=13")
    restarted = CoreStore(store.database_path)
    assert restarted.migrate() == 15
    with restarted.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM context_user_mutations WHERE record_id=?",
                (record_id,),
            ).fetchone()
            is None
        )


def test_manual_approval_override_rekeys_candidate_and_preserves_delete_barrier(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"approval override archive",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    candidate = store.add_candidate(
        CandidateInput(
            kind="old-kind",
            content="Original candidate value",
            source_id=source.id,
            source_reference="message:old",
            source_service="fixture-provider",
            source_type="provider_archive",
            entity_key="old-entity",
            attribute_key="old-attribute",
        )
    )
    approved = store.approve_candidate(
        candidate.id,
        ApprovalRequest(
            kind="final-kind",
            content="Final approved value",
            structured_value={"canonical": "final"},
            entity_key="Final Entity",
            attribute_key="Final Attribute",
            source_reference="message:final",
        ),
    )
    with store.connect() as connection:
        keys = connection.execute(
            "SELECT c.record_key AS candidate_key,r.record_key AS record_key "
            "FROM context_candidates c JOIN context_records r ON r.id=c.record_id "
            "WHERE c.id=?",
            (candidate.id,),
        ).fetchone()
    assert keys is not None
    assert keys["candidate_key"] == keys["record_key"]

    store.delete_record(approved.id, reason="override deletion barrier")
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
        idempotency_key="override-reimport",
    )
    session_id = str(session["session_id"])
    reimport = store.submit_batch(
        session_id,
        "override-reimport-batch",
        [
            CandidateInput(
                kind="final-kind",
                content="Final approved value",
                structured_value={"canonical": "final"},
                entity_key="final entity",
                attribute_key="final attribute",
                source_id=source.id,
                source_reference="message:final",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        session_id,
        CoverageReport(available=["fixture-provider"], complete=True),
    )
    observed = store.get_observation(str(reimport["candidate_ids"][0]))
    assert observed.disposition == ObservationDisposition.IGNORED
    assert observed.record_id == approved.id
    assert store.status()["counts"]["active_records"] == 0


def test_user_deletion_tombstone_cannot_be_reapplied_by_archive_import(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )

    first_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="initial",
    )
    first = store.get_observation(first_id)
    assert first.record_id is not None
    record_id = first.record_id
    tombstone = store.delete_record(record_id, reason="user explicitly removed this memory")

    second_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="reimport",
    )
    second = store.get_observation(second_id)
    assert second.disposition == ObservationDisposition.IGNORED
    assert second.record_id == record_id
    assert "blocked by an explicit deletion" in (second.decision_reason or "")
    assert store.get_memory_truth(record_id).status == MemoryTruthStatus.DELETED
    assert store.status()["counts"]["active_records"] == 0
    with store.connect() as connection:
        row = connection.execute(
            "SELECT deletion_origin FROM deletion_tombstones WHERE record_id=?",
            (record_id,),
        ).fetchone()
    assert row is not None
    assert row["deletion_origin"] == "ordinary"
    assert tombstone["record_id"] == record_id


def test_same_source_reference_different_values_do_not_collapse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )

    first_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:collision",
        session_key="collision-first",
    )
    first = store.get_observation(first_id)
    assert first.record_id is not None
    record_id = first.record_id
    store.delete_record(record_id, reason="ordinary deletion for collision fixture")

    second_id = _archive_observation(
        store,
        source.id,
        content="I prefer detailed answers.",
        source_reference="message:collision",
        session_key="collision-second",
    )
    second = store.get_observation(second_id)
    assert second.record_id is not None
    assert second.record_id != record_id
    assert store.get_memory_truth(record_id).status == MemoryTruthStatus.DELETED


def test_updated_identity_key_keeps_deletion_barrier_on_matching_reimport(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )
    first_id = _archive_observation(
        store,
        source.id,
        content="V1 preference",
        source_reference="message:identity-update",
        session_key="v1",
        entity_key="synthetic-user",
        attribute_key="preference",
    )
    first = store.get_observation(first_id)
    assert first.record_id is not None
    record_id = first.record_id

    second_id = _archive_observation(
        store,
        source.id,
        content="V2 preference",
        source_reference="message:identity-update",
        session_key="v2",
        entity_key="synthetic-user",
        attribute_key="preference",
    )
    second = store.get_observation(second_id)
    assert second.record_id == record_id
    with store.connect() as connection:
        key = connection.execute(
            "SELECT record_key FROM context_records WHERE id=?", (record_id,)
        ).fetchone()[0]
    assert key is not None

    store.delete_record(record_id, reason="user removed the V2 memory")
    reimport_id = _archive_observation(
        store,
        source.id,
        content="V2 preference",
        source_reference="message:identity-update",
        session_key="v2-reimport",
        entity_key="synthetic-user",
        attribute_key="preference",
    )
    reimport = store.get_observation(reimport_id)
    assert reimport.disposition == ObservationDisposition.IGNORED
    assert reimport.record_id == record_id
    assert store.status()["counts"]["active_records"] == 0


def test_manual_approval_links_originating_evidence_once_on_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = store.add_candidate(
        CandidateInput(kind="fact", content="Manual approval evidence fixture")
    )
    record = store.approve_candidate(candidate.id)
    store.approve_candidate(candidate.id)
    with store.connect() as connection:
        links = connection.execute(
            "SELECT relationship FROM context_observation_links "
            "WHERE observation_id=? AND record_id=?",
            (candidate.id, record.id),
        ).fetchall()
    assert [str(row[0]) for row in links] == ["applied"]
    assert [item.observation_id for item in store.get_memory_truth(record.id).evidence] == [
        candidate.id
    ]


def test_truth_projection_bounds_large_supersession_and_evidence_sets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    base = store.approve_candidate(
        store.add_candidate(CandidateInput(kind="fact", content="Bounded truth base")).id
    )

    session = store.begin_ingestion(
        mode=IngestionMode.BOOTSTRAP,
        accessible_sources=[],
        unavailable_sources=[],
        idempotency_key="bounded-superseders",
    )
    superseders = store.submit_batch(
        str(session["session_id"]),
        "bounded-superseders-batch",
        [
            CandidateInput(
                kind="fact",
                content=f"Superseding value {index}",
                supersedes=base.id,
            )
            for index in range(70)
        ],
    )
    for candidate_id in superseders["candidate_ids"]:
        store.approve_candidate(str(candidate_id))
    truth = store.get_memory_truth(base.id)
    assert truth.status == MemoryTruthStatus.SUPERSEDED
    assert len(truth.superseded_by) == 64

    evidence_record = store.approve_candidate(
        store.add_candidate(CandidateInput(kind="fact", content="Bounded evidence value")).id
    )
    for _ in range(512):
        store.add_candidate(CandidateInput(kind="fact", content="Bounded evidence value"))
    evidence = store.get_memory_truth(evidence_record.id).evidence
    assert len(evidence) == 512


def test_truth_page_select_count_is_bounded_by_page_not_database_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    for index in range(8):
        store.approve_candidate(
            store.add_candidate(CandidateInput(kind="fact", content=f"Page fixture {index}")).id
        )

    original_connect = store.connect

    def measure_selects(expected_total: int) -> int:
        statements: list[str] = []

        def traced_connect() -> object:
            connection = original_connect()
            connection.set_trace_callback(
                lambda statement: (
                    statements.append(statement)
                    if statement.lstrip().upper().startswith(("SELECT", "WITH"))
                    else None
                )
            )
            return connection

        monkeypatch.setattr(store, "connect", traced_connect)
        try:
            response = store.list_memory_truth(limit=2, offset=0)
            assert response.total == expected_total
            assert len(response.items) == 2
        finally:
            monkeypatch.setattr(store, "connect", original_connect)
        return len(statements)

    small_page_selects = measure_selects(8)
    for index in range(120):
        store.approve_candidate(
            store.add_candidate(
                CandidateInput(kind="fact", content=f"Larger database fixture {index}")
            ).id
        )
    large_page_selects = measure_selects(128)
    assert small_page_selects <= 16
    assert large_page_selects <= small_page_selects + 1


def test_truth_projection_exposes_evidence_times_and_content_free_coverage(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path)
    with TestClient(create_app(config)) as client:
        proposed = client.post(
            "/v1/ingestion/propose",
            json={
                "kind": "fact",
                "content": "The synthetic truth endpoint is local.",
                "evidence": "explicit synthetic fixture",
                "observed_at": "2026-01-02T03:04:05+00:00",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "explicit_user_statement": True,
            },
        )
        assert proposed.status_code == 200, proposed.text
        record_id = str(proposed.json()["record_id"])

        truth = client.get(f"/v1/context/truth/{record_id}")
        assert truth.status_code == 200, truth.text
        payload = truth.json()
        assert payload["status"] == "current"
        assert payload["record"]["status"] == "current"
        assert payload["evidence"][0]["observation_id"] == proposed.json()["id"]
        assert payload["evidence"][0]["effective_at"] == "2026-01-01T00:00:00+00:00"
        assert payload["evidence"][0]["observed_at"] == "2026-01-02T03:04:05+00:00"
        assert payload["evidence"][0]["recorded_at"]
        assert payload["history_count"] == 1

        coverage = client.get("/v1/context/coverage")
        assert coverage.status_code == 200, coverage.text
        coverage_payload = coverage.json()
        assert coverage_payload["record_count"] == 1
        assert coverage_payload["observations_by_disposition"]["applied"] == 1
        assert "content" not in coverage_payload

        admin_truth = client.get("/v1/admin/memory-truth")
        assert admin_truth.status_code == 200, admin_truth.text
        assert admin_truth.json()["items"][0]["record"]["id"] == record_id


def test_truth_detail_filters_disjoint_canonical_and_evidence_acl(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="old disjoint-private value",
            structured_value={"private": "old structured value"},
            entity_key="user",
            attribute_key="private_fact",
            scopes=["project:old-private"],
            tags=["old-private-tag"],
            source_reference="old-private-reference",
            source_service="old-private-service",
            source_type="old-private-type",
            allowed_clients=["old-client"],
            evidence="old disjoint-private evidence",
            valid_from="2026-01-01T00:00:00+00:00",
            expires_at="2027-01-01T00:00:00+00:00",
            explicit_user_statement=True,
        )
    )
    assert original.record_id is not None
    correction = store.add_candidate(
        CandidateInput(
            kind="correction",
            content="new disjoint-private value",
            supersedes=original.record_id,
            allowed_clients=["new-client"],
            evidence="new disjoint-private evidence",
            explicit_user_statement=True,
        )
    )
    assert correction.disposition == ObservationDisposition.APPLIED
    assert store.get_record(original.record_id).allowed_clients == ["new-client"]

    old_principal = ClientPrincipal("old-client", "Old client", frozenset())
    new_principal = ClientPrincipal("new-client", "New client", frozenset())
    with pytest.raises(NotFoundError):
        store.get_memory_truth(original.record_id, principal=old_principal)

    authorized_truth = store.get_memory_truth(original.record_id, principal=new_principal)
    assert authorized_truth.record.content == "new disjoint-private value"
    assert authorized_truth.record.evidence == "new disjoint-private evidence"
    assert authorized_truth.record.structured_value is None
    assert authorized_truth.record.scopes == []
    assert authorized_truth.record.tags == []
    assert authorized_truth.record.source_id is None
    assert authorized_truth.record.source_reference is None
    assert authorized_truth.record.source_service is None
    assert authorized_truth.record.source_type is None
    assert authorized_truth.record.valid_from is None
    assert authorized_truth.record.expires_at is None
    assert [item.content for item in authorized_truth.evidence] == ["new disjoint-private value"]
    assert [item.evidence for item in authorized_truth.evidence] == [
        "new disjoint-private evidence"
    ]

    # The principal-less local-admin/Core path intentionally retains the full
    # canonical projection and all linked evidence.
    local_truth = store.get_memory_truth(original.record_id)
    assert [item.content for item in local_truth.evidence] == [
        "old disjoint-private value",
        "new disjoint-private value",
    ]

    unrestricted = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="unrestricted local value",
            evidence="unrestricted local evidence",
            explicit_user_statement=True,
        )
    )
    assert unrestricted.record_id is not None
    unrestricted_truth = store.get_memory_truth(
        unrestricted.record_id,
        principal=old_principal,
    )
    assert unrestricted_truth.record.content == "unrestricted local value"
    assert [item.evidence for item in unrestricted_truth.evidence] == [
        "unrestricted local evidence"
    ]


def test_truth_status_distinguishes_conflict_supersession_and_deletion(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.approve_candidate(
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content="The synthetic city is Boston.",
                entity_key="synthetic-user",
                attribute_key="city",
            )
        ).id
    )
    peer = store.approve_candidate(
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content="The synthetic city is Chicago.",
                entity_key="synthetic-user",
                attribute_key="city",
            )
        ).id
    )
    assert store.get_memory_truth(first.id).status == MemoryTruthStatus.CONFLICTED
    assert store.get_memory_truth(peer.id).status == MemoryTruthStatus.CONFLICTED

    replacement = store.approve_candidate(
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content="The synthetic city is Seattle.",
                entity_key="synthetic-user",
                attribute_key="city",
                supersedes=first.id,
            )
        ).id
    )
    assert store.get_memory_truth(first.id).status == MemoryTruthStatus.SUPERSEDED
    store.delete_record(replacement.id, reason="synthetic deletion")
    assert store.get_memory_truth(replacement.id).status == MemoryTruthStatus.DELETED
