from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from allthecontext.export import (
    _decrypt_file,
    _encrypt_file,
    create_export,
    restore_export,
)
from allthecontext.models import (
    Availability,
    CandidateInput,
    CoverageReport,
    IngestionMode,
)
from allthecontext.recovery_admin import (
    carry_forward_purge_tombstones,
    restore_isolated,
)
from allthecontext.storage import (
    CoreStore,
    NotFoundError,
    _mutation_evidence_hash,
    source_rebuild_marker,
)

PASSPHRASE = "memory-truth-authority-passphrase"


def _archive_record(store: CoreStore, source_id: str, *, key: str, content: str) -> str:
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=f"authority-session-{key}",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        f"authority-batch-{key}",
        [
            CandidateInput(
                kind="preference",
                content=content,
                source_id=source_id,
                source_reference=f"message:{key}",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fixture-archive"], complete=True),
    )
    observation = store.get_observation(str(batch["candidate_ids"][0]))
    assert observation.record_id is not None
    return observation.record_id


def _publish_rebuild(
    store: CoreStore, source_id: str, *, key: str, content: str
) -> tuple[str, str]:
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=f"archive:{source_id}:fixture:rebuild:1",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        f"authority-rebuild-batch-{key}",
        [
            CandidateInput(
                kind="preference",
                content=content,
                source_id=source_id,
                source_reference=f"message:{key}",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fixture-archive"], complete=True),
        publish=False,
    )
    source = store.get_source(source_id)
    marker = source_rebuild_marker(source_id, source.content_hash, 1)
    store.update_source_import(
        source_id,
        import_status="processing",
        metadata={
            "rebuild_generation": 1,
            "rebuild_in_progress": True,
            "rebuild_source_marker": marker,
        },
        parser_warnings=[],
    )
    store.publish_source_rebuild(source_id, str(session["session_id"]), rebuild_generation=1)
    observation = store.get_observation(str(batch["candidate_ids"][0]))
    assert observation.record_id is not None
    return observation.record_id, str(observation.disposition)


def _rewrite_with_forged_ledger_row(
    source: Path,
    destination: Path,
    passphrase: str,
    *,
    record_id: str,
    vault_id: str,
) -> None:
    plain = source.parent / "decrypted.zip"
    rebuilt = source.parent / "rebuilt.zip"
    _decrypt_file(source, plain, passphrase)
    with zipfile.ZipFile(plain) as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    manifest = json.loads(entries["manifest.json"])
    versions_name = "tables/context_record_versions.jsonl"
    version_rows = [
        json.loads(line) for line in entries[versions_name].decode().splitlines() if line.strip()
    ]
    history = next(row for row in version_rows if str(row["record_id"]) == record_id)
    # This is the exact evidence-only forgery: the source ledger is empty, the
    # history row merely looks like a non-user restore, and the forged ledger
    # row recomputes the old generic digest and intent coordinate.
    history["reason"] = "record_restored"
    history["user_action_kind"] = None
    history["user_action_key"] = None
    version_bytes = (
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in version_rows
        )
    ).encode()
    entries[versions_name] = version_bytes
    manifest["sha256"][versions_name] = hashlib.sha256(version_bytes).hexdigest()
    ledger_name = "tables/context_user_mutations.jsonl"
    forged = {
        "id": "forged-ledger-row",
        "vault_id": vault_id,
        "record_id": record_id,
        "mutation_kind": "restore",
        "mutation_origin": "local_user",
        "actor": "local-user",
        "created_at": "2026-08-22T00:00:00Z",
        "evidence_kind": "record_version",
        "evidence_id": history["id"],
        "evidence_version": history["version"],
        "evidence_hash": _mutation_evidence_hash(
            mutation_kind="restore",
            evidence_kind="record_version",
            vault_id=vault_id,
            record_id=record_id,
            evidence_id=str(history["id"]),
            evidence_version=int(history["version"]),
            snapshot_json=json.dumps(
                json.loads(history["snapshot_json"]),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        ),
        "intent_key": f"restore:{record_id}:{history['version']}",
    }
    ledger_bytes = (json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n").encode()
    entries[ledger_name] = ledger_bytes
    manifest["tables"]["context_user_mutations"] = 1
    manifest["sha256"][ledger_name] = hashlib.sha256(ledger_bytes).hexdigest()
    entries["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with zipfile.ZipFile(rebuilt, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    _encrypt_file(rebuilt, destination, passphrase)


def test_forged_authenticated_ledger_row_is_ignored_and_rebuild_reuses_id(
    tmp_path: Path,
) -> None:
    source_db = tmp_path / "source.sqlite3"
    source_store = CoreStore(source_db)
    source_store.initialize_vault()
    source = source_store.add_source(
        b"authority fixture archive",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    record_id = _archive_record(
        source_store, source.id, key="forged", content="I prefer concise answers."
    )
    package = tmp_path / "source.atcexp"
    create_export(source_db, package, PASSPHRASE, include_sources=True)
    forged_package = tmp_path / "forged.atcexp"
    _rewrite_with_forged_ledger_row(
        package,
        forged_package,
        PASSPHRASE,
        record_id=record_id,
        vault_id=source_store.vault_id(),
    )

    restored_db = tmp_path / "restored.sqlite3"
    restored = CoreStore(restored_db)
    restored.initialize_vault()
    result = restore_export(forged_package, restored_db, PASSPHRASE)
    assert result["user_mutations"] == {"accepted": 0, "ignored": 1}
    with restored.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM context_user_mutations").fetchone()[0] == 0

    session = restored.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
        idempotency_key=f"archive:{source.id}:fixture:rebuild:1",
    )
    batch = restored.submit_batch(
        str(session["session_id"]),
        "forged-rebuild-batch",
        [
            CandidateInput(
                kind="preference",
                content="I prefer concise answers.",
                source_id=source.id,
                source_reference="message:forged",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    restored.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fixture-archive"], complete=True),
        publish=False,
    )
    marker = source_rebuild_marker(source.id, restored.get_source(source.id).content_hash, 1)
    restored.update_source_import(
        source.id,
        import_status="processing",
        metadata={
            "rebuild_generation": 1,
            "rebuild_in_progress": True,
            "rebuild_source_marker": marker,
        },
        parser_warnings=[],
    )
    assert restored.publish_source_rebuild(
        source.id, str(session["session_id"]), rebuild_generation=1
    )
    assert restored.get_observation(str(batch["candidate_ids"][0])).record_id == record_id


def test_genuine_typed_mutations_round_trip_and_block_rebuild(tmp_path: Path) -> None:
    source_db = tmp_path / "genuine-source.sqlite3"
    source_store = CoreStore(source_db)
    source_store.initialize_vault()
    cases: dict[str, tuple[str, str]] = {}
    for kind in ("correction", "availability", "restore", "delete", "source-delete"):
        source = source_store.add_source(
            f"{kind} archive".encode(),
            source_service="fixture-provider",
            source_type="provider_archive",
        )
        record_id = _archive_record(
            source_store, source.id, key=kind, content=f"Original {kind} memory."
        )
        cases[kind] = (source.id, record_id)
        if kind == "correction":
            source_store.correct_record(
                record_id, content="Corrected memory.", reason="user correction"
            )
        elif kind == "availability":
            source_store.change_availability(record_id, Availability.LOCAL)
        elif kind == "restore":
            source_store.delete_record(record_id, reason="delete before restore")
            source_store.restore_record(record_id, reason="restore by user")
        elif kind == "delete":
            source_store.delete_record(record_id, reason="delete by user")
        else:
            source_store.delete_source(source.id, reason="delete source by user")

    with source_store.connect() as connection:
        source_mutations = connection.execute(
            "SELECT mutation_kind,evidence_kind FROM context_user_mutations ORDER BY created_at,id"
        ).fetchall()
    assert {str(row["evidence_kind"]) for row in source_mutations} == {"user_action"}

    package = tmp_path / "genuine.atcexp"
    create_export(source_db, package, PASSPHRASE, include_sources=True)
    destination_db = tmp_path / "genuine-destination.sqlite3"
    destination = CoreStore(destination_db)
    destination.initialize_vault()
    result = restore_export(package, destination_db, PASSPHRASE)
    assert result["user_mutations"] == {
        "accepted": len(source_mutations),
        "ignored": 0,
    }
    repeated = restore_export(package, destination_db, PASSPHRASE)
    assert repeated["user_mutations"] == {
        "accepted": 0,
        "ignored": len(source_mutations),
    }
    with destination.connect() as connection:
        destination_mutations = connection.execute(
            "SELECT mutation_kind,evidence_kind FROM context_user_mutations "
            "WHERE evidence_kind='user_action' ORDER BY created_at,id"
        ).fetchall()
    assert [tuple(row) for row in destination_mutations] == [tuple(row) for row in source_mutations]

    blocked_source_id, blocked_record_id = cases["delete"]
    rebuild_record_id, rebuild_disposition = _publish_rebuild(
        destination,
        blocked_source_id,
        key="delete",
        content="Original delete memory.",
    )
    assert rebuild_record_id == blocked_record_id
    assert rebuild_disposition == "ignored"
    with destination.connect() as connection:
        assert (
            connection.execute(
                "SELECT deleted_at IS NOT NULL FROM context_records WHERE id=?",
                (blocked_record_id,),
            ).fetchone()[0]
            == 1
        )


def test_isolated_restore_carries_local_barrier_idempotently_and_handles_purge(
    tmp_path: Path,
) -> None:
    active = tmp_path / "active"
    store = CoreStore(active / "core.sqlite3")
    store.initialize_vault()
    source = store.add_source(
        b"isolated restore fixture",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    record_id = _archive_record(store, source.id, key="carry", content="Carry this memory.")
    package = tmp_path / "before-mutation.atcexp"
    create_export(active / "core.sqlite3", package, PASSPHRASE, include_sources=True)
    store.delete_record(record_id, reason="destination-only barrier")

    isolated = tmp_path / "isolated"
    restored = restore_isolated(
        package,
        data_dir=active,
        destination=isolated,
        passphrase=PASSPHRASE,
        dry_run=False,
    )
    assert restored["purge_carry_forward"]["carried_user_mutations"] == 1
    isolated_store = CoreStore(isolated / "core.sqlite3")
    with isolated_store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 1
        )
    repeated_carry = carry_forward_purge_tombstones(
        active / "core.sqlite3", isolated / "core.sqlite3"
    )
    assert repeated_carry["carried_user_mutations"] == 0
    with isolated_store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (record_id,)
            ).fetchone()[0]
            == 1
        )

    purged_active = tmp_path / "purged-active"
    purged_store = CoreStore(purged_active / "core.sqlite3")
    purged_store.initialize_vault()
    purged_source = purged_store.add_source(
        b"purged isolated fixture",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    purged_record_id = _archive_record(
        purged_store, purged_source.id, key="purged", content="Purged barrier memory."
    )
    purged_package = tmp_path / "before-purge.atcexp"
    create_export(purged_active / "core.sqlite3", purged_package, PASSPHRASE, include_sources=True)
    purged_store.delete_record(purged_record_id, reason="purge boundary")
    purged_store.purge(
        "record",
        purged_record_id,
        confirmation=purged_store.purge_confirmation_phrase("record", purged_record_id),
        compact=False,
    )
    purged_isolated = tmp_path / "purged-isolated"
    purged_result = restore_isolated(
        purged_package,
        data_dir=purged_active,
        destination=purged_isolated,
        passphrase=PASSPHRASE,
        dry_run=False,
    )
    assert purged_result["purge_carry_forward"]["carried_user_mutations"] == 1
    purged_restored = CoreStore(purged_isolated / "core.sqlite3")
    with pytest.raises(NotFoundError):
        purged_restored.get_record(purged_record_id)
    with purged_restored.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (purged_record_id,)
            ).fetchone()[0]
            == 1
        )


def test_legacy_inference_is_source_typed_and_excludes_manual_and_rebuild_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    store = CoreStore(database)
    store.initialize_vault()
    source = store.add_source(
        b"legacy authority fixture",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    sourced_id = _archive_record(store, source.id, key="legacy", content="Source lineage.")
    store.correct_record(sourced_id, content="Corrected lineage.", reason="private correction note")
    store.change_availability(sourced_id, Availability.LOCAL)
    manual = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="Brand-new manual record.",
            explicit_user_statement=True,
        )
    )
    assert manual.record_id is not None
    store.correct_record(manual.record_id, content="Manual correction.", reason="manual note")

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE context_user_mutations")
        connection.execute("DELETE FROM schema_migrations WHERE version=13")
    restarted = CoreStore(database)
    restarted.migrate()
    with restarted.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (sourced_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?", (manual.record_id,)
            ).fetchone()[0]
            == 0
        )

    automatic_database = tmp_path / "automatic-rebuild.sqlite3"
    automatic = CoreStore(automatic_database)
    automatic.initialize_vault()
    automatic_source = automatic.add_source(
        b"automatic rebuild fixture",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    automatic_record_id = _archive_record(
        automatic,
        automatic_source.id,
        key="automatic",
        content="Automatic lineage.",
    )
    session = automatic.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[automatic_source.id],
        unavailable_sources=[],
        idempotency_key=f"archive:{automatic_source.id}:fixture:rebuild:1",
    )
    automatic.submit_batch(
        str(session["session_id"]),
        "automatic-rebuild-batch",
        [
            CandidateInput(
                kind="preference",
                content="Automatic lineage.",
                source_id=automatic_source.id,
                source_reference="message:automatic",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    automatic.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fixture-archive"], complete=True),
        publish=False,
    )
    marker = source_rebuild_marker(automatic_source.id, automatic_source.content_hash, 1)
    automatic.update_source_import(
        automatic_source.id,
        import_status="processing",
        metadata={
            "rebuild_generation": 1,
            "rebuild_in_progress": True,
            "rebuild_source_marker": marker,
        },
        parser_warnings=[],
    )
    automatic.publish_source_rebuild(
        automatic_source.id, str(session["session_id"]), rebuild_generation=1
    )
    with sqlite3.connect(automatic_database) as connection:
        connection.execute("DROP TABLE context_user_mutations")
        connection.execute("DELETE FROM schema_migrations WHERE version=13")
    automatic_restarted = CoreStore(automatic_database)
    automatic_restarted.migrate()
    with automatic_restarted.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations WHERE record_id=?",
                (automatic_record_id,),
            ).fetchone()[0]
            == 0
        )


def test_restore_source_delete_and_actor_reason_material_is_content_free(
    tmp_path: Path,
) -> None:
    marker = "SECRET-MEMORY-TRUTH-MARKER"
    database = tmp_path / "secrets.sqlite3"
    store = CoreStore(database)
    store.initialize_vault()
    source = store.add_source(
        b"safe source bytes",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    record_id = _archive_record(store, source.id, key="secret", content="Safe memory.")
    deletion = store.delete_source(source.id, reason=marker, actor=marker)
    assert deletion["reason"] == "source_deleted"
    store.restore_source(source.id, reason=marker, actor=marker)
    store.delete_record(record_id, reason=marker, actor=marker)
    store.restore_record(record_id, reason=marker, actor=marker)
    package = tmp_path / "secrets.atcexp"
    create_export(database, package, PASSPHRASE, include_sources=True, include_audit=True)

    with store.connect() as connection:
        durable = b"".join(
            bytes(value)
            for table in (
                "context_user_mutations",
                "context_record_versions",
                "deletion_tombstones",
                "source_records",
                "audit_events",
            )
            for row in connection.execute(f'SELECT * FROM "{table}"')
            for value in row
            if isinstance(value, (bytes, bytearray))
        )
        text_material = json.dumps(
            [
                dict(row)
                for table in (
                    "context_user_mutations",
                    "context_record_versions",
                    "deletion_tombstones",
                    "source_records",
                    "audit_events",
                )
                for row in connection.execute(f'SELECT * FROM "{table}"')
            ],
            default=str,
        )
    assert marker.encode() not in durable
    assert marker not in text_material
    assert marker.encode() not in package.read_bytes()
    assert all(marker not in json.dumps(item, default=str) for item in store.list_audit())
    assert all(
        marker not in json.dumps(item, default=str) for item in store.record_history(record_id)
    )


def test_already_current_restore_has_one_stable_barrier_per_intent(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "current.sqlite3")
    store.initialize_vault()
    record = store.add_candidate(
        CandidateInput(kind="fact", content="Already current.", explicit_user_statement=True)
    )
    assert record.record_id is not None
    first = store.restore_record(record.record_id, reason="first restore intent")
    second = store.restore_record(record.record_id, reason="retry restore intent")
    assert first.version == second.version == 2
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_user_mutations "
                "WHERE record_id=? AND mutation_kind='restore'",
                (record.record_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_record_versions WHERE record_id=?",
                (record.record_id,),
            ).fetchone()[0]
            == 2
        )
