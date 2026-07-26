from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
import zipfile
from pathlib import Path

import pytest
from allthecontext import cli
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.export import _database_to_zip, _encrypt_file, create_export, restore_export
from allthecontext.models import CandidateInput
from allthecontext.relay.service import (
    ClientIdentity,
    RelayService,
    SecretLikeProposalRefused,
    SQLiteRelayStore,
)
from fastapi.testclient import TestClient

PASSPHRASE = "synthetic boundary passphrase"
CANARY = "ATC_SYNTHETIC_SECRET_7Q2Z9M"
SECRET_TEXT = f"password: {CANARY}"


def _assert_absent_from_paths(root: Path, needles: tuple[bytes, ...]) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_bytes()
        for needle in needles:
            assert needle not in content, path


def _database_text_values(database: Path) -> list[str]:
    values: list[str] = []
    with sqlite3.connect(database) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = [
                (str(row[1]), str(row[2]).casefold())
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            text_columns = [name for name, kind in columns if kind in {"text", ""}]
            if not text_columns:
                continue
            quoted = ",".join(f'"{name}"' for name in text_columns)
            for row in connection.execute(f'SELECT {quoted} FROM "{table}"'):
                values.extend(str(value) for value in row if value is not None)
    return values


def test_direct_secret_refusal_is_content_free_and_replayable(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    operation_id = str(uuid.uuid4())
    with TestClient(create_app(config)) as client:
        payload = {
            "kind": "fact",
            "content": SECRET_TEXT,
            "structured_value": {"client_secret": CANARY},
            "evidence": f"access_token={CANARY}",
            "explicit_user_statement": True,
            "idempotency_key": operation_id,
        }
        first = client.post("/v1/ingestion/propose", json=payload)
        mutated = client.post(
            "/v1/ingestion/propose",
            json={**payload, "content": f"password: {CANARY}_MUTATED"},
        )

        assert first.status_code == 200, first.text
        assert mutated.status_code == 200, mutated.text
        assert mutated.json()["id"] == first.json()["id"]
        assert mutated.json()["replayed"] is True
        assert first.json() == {
            "id": first.json()["id"],
            "refused": True,
            "disposition": "ignored",
            "reason_code": "direct_secret_like_content",
            "detector_version": "direct-secret-v1",
            "created_at": first.json()["created_at"],
            "replayed": False,
            "user_action_required": True,
        }
        assert client.get("/v1/admin/observations").json()["total"] == 0
        diagnostics = {
            "status": client.get("/v1/context/status").json(),
            "audit": client.get("/v1/admin/audit").json(),
        }
        assert CANARY not in json.dumps(diagnostics)

    candidate_digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    text_values = "\n".join(_database_text_values(config.database_path))
    assert CANARY not in text_values
    assert candidate_digest not in text_values
    _assert_absent_from_paths(
        tmp_path,
        (CANARY.encode(), candidate_digest.encode()),
    )


def test_batch_correction_and_forget_keep_direct_canary_out_of_storage(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        record = client.post(
            "/v1/ingestion/propose",
            json={
                "kind": "fact",
                "content": "The synthetic account uses a rotating credential.",
                "explicit_user_statement": True,
            },
        ).json()
        record_id = str(record["record_id"])
        session = client.post(
            "/v1/ingestion/begin",
            json={
                "mode": "ongoing",
                "accessible_sources": ["synthetic"],
                "unavailable_sources": [],
            },
        ).json()
        batch_key = str(uuid.uuid4())
        batch = client.post(
            "/v1/ingestion/batch",
            json={
                "session_id": session["session_id"],
                "idempotency_key": batch_key,
                "candidates": [
                    {
                        "kind": "fact",
                        "content": SECRET_TEXT,
                        "explicit_user_statement": True,
                    }
                ],
            },
        )
        correction = client.post(
            "/v1/ingestion/error",
            json={
                "record_id": record_id,
                "description": f"client_secret: {CANARY}",
                "suggested_correction": "The synthetic account rotates credentials weekly.",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        admin_correction = client.post(
            f"/v1/admin/records/{record_id}/correct",
            json={
                "content": f"api_key={CANARY}",
                "reason": "Synthetic boundary check",
            },
        )
        forgotten = client.post(
            "/v1/ingestion/forget",
            json={"record_id": record_id, "reason": SECRET_TEXT},
        )

        assert batch.status_code == 200, batch.text
        assert batch.json()["candidate_ids"] == []
        assert batch.json()["refused_count"] == 1
        assert batch.json()["refusal_reason"] == "direct_secret_like_content"
        assert correction.status_code == 200, correction.text
        assert correction.json()["refused"] is True
        assert admin_correction.status_code == 200, admin_correction.text
        assert admin_correction.json()["refused"] is True
        assert forgotten.status_code == 200, forgotten.text

    assert CANARY not in "\n".join(_database_text_values(config.database_path))
    _assert_absent_from_paths(tmp_path, (CANARY.encode(),))


def test_secret_batch_rejects_content_derived_retry_verifier_before_storage(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    content_derived_key = hashlib.sha256(SECRET_TEXT.encode()).hexdigest()
    with TestClient(create_app(config)) as client:
        session = client.post(
            "/v1/ingestion/begin",
            json={
                "mode": "ongoing",
                "accessible_sources": ["synthetic"],
                "unavailable_sources": [],
            },
        ).json()
        response = client.post(
            "/v1/ingestion/batch",
            json={
                "session_id": session["session_id"],
                "idempotency_key": content_derived_key,
                "candidates": [
                    {
                        "kind": "fact",
                        "content": SECRET_TEXT,
                        "explicit_user_statement": True,
                    }
                ],
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_state"
        assert client.get("/v1/admin/observations").json()["total"] == 0

    _assert_absent_from_paths(
        tmp_path,
        (CANARY.encode(), content_derived_key.encode()),
    )


def test_startup_repair_compacts_legacy_sqlite_wal_fts_and_export_restore(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source = CoreService(CoreConfig.in_directory(source_dir, require_auth=False))
    legacy = source.store.add_candidate(
        CandidateInput(
            kind="fact",
            content="Synthetic placeholder before legacy corruption.",
            explicit_user_statement=False,
            idempotency_key="legacy-operation",
        )
    )
    with sqlite3.connect(source.config.database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "UPDATE context_candidates SET content=?,evidence=?,content_hash=?,"
            "disposition='ignored',approval_status='rejected' WHERE id=?",
            (SECRET_TEXT, SECRET_TEXT, hashlib.sha256(SECRET_TEXT.encode()).hexdigest(), legacy.id),
        )
        connection.execute(
            "INSERT INTO context_errors"
            "(id,vault_id,record_id,candidate_id,description,created_at) "
            "VALUES(?,?,?,?,?,datetime('now'))",
            ("synthetic-error", source.store.vault_id(), None, legacy.id, SECRET_TEXT),
        )
        connection.execute(
            "INSERT INTO context_fts(record_id,content,kind,tags,scopes) VALUES(?,?,?,?,?)",
            ("synthetic-stale-fts", SECRET_TEXT, "fact", "", ""),
        )
        connection.commit()

    repaired = CoreService(CoreConfig.in_directory(source_dir, require_auth=False))
    with sqlite3.connect(repaired.config.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE id=?", (legacy.id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_fts WHERE content MATCH ?", (CANARY,)
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    _assert_absent_from_paths(source_dir, (CANARY.encode(),))

    package = tmp_path / "safe.atcexp"
    manifest = create_export(
        repaired.config.database_path,
        package,
        PASSPHRASE,
        include_audit=True,
    )
    assert CANARY not in json.dumps(manifest)
    assert CANARY.encode() not in package.read_bytes()

    restored_dir = tmp_path / "restored"
    restored = CoreService(CoreConfig.in_directory(restored_dir, require_auth=False))
    restore_export(package, restored.config.database_path, PASSPHRASE)
    assert CANARY not in "\n".join(_database_text_values(restored.config.database_path))
    _assert_absent_from_paths(restored_dir, (CANARY.encode(),))


def test_restore_repairs_legacy_export_before_returning(tmp_path: Path) -> None:
    unsafe_dir = tmp_path / "unsafe"
    unsafe = CoreService(CoreConfig.in_directory(unsafe_dir, require_auth=False))
    candidate = unsafe.store.add_candidate(
        CandidateInput(kind="fact", content="Temporary synthetic row.")
    )
    with sqlite3.connect(unsafe.config.database_path) as connection:
        connection.execute(
            "UPDATE context_candidates SET content=?,evidence=?,disposition='ignored' WHERE id=?",
            (SECRET_TEXT, SECRET_TEXT, candidate.id),
        )
        connection.commit()

    zip_path = tmp_path / "legacy.zip"
    _database_to_zip(
        unsafe.config.database_path,
        zip_path,
        include_sources=False,
        include_audit=True,
    )
    with zipfile.ZipFile(zip_path) as archive:
        assert CANARY.encode() in archive.read("tables/context_candidates.jsonl")
    legacy_export = tmp_path / "legacy.atcexp"
    _encrypt_file(zip_path, legacy_export, PASSPHRASE)

    destination_dir = tmp_path / "destination"
    destination = CoreService(CoreConfig.in_directory(destination_dir, require_auth=False))
    restore_export(legacy_export, destination.config.database_path, PASSPHRASE)

    assert CANARY not in "\n".join(_database_text_values(destination.config.database_path))
    _assert_absent_from_paths(destination_dir, (CANARY.encode(),))


def test_export_repairs_a_migrated_core_before_writing_package_bytes(tmp_path: Path) -> None:
    source_dir = tmp_path / "export-repair"
    source = CoreService(CoreConfig.in_directory(source_dir, require_auth=False))
    candidate = source.store.add_candidate(
        CandidateInput(kind="fact", content="Temporary synthetic row.")
    )
    with sqlite3.connect(source.config.database_path) as connection:
        connection.execute(
            "UPDATE context_candidates SET content=?,evidence=?,disposition='ignored' WHERE id=?",
            (SECRET_TEXT, SECRET_TEXT, candidate.id),
        )
        connection.commit()

    package = tmp_path / "repaired-before-export.atcexp"
    create_export(source.config.database_path, package, PASSPHRASE, include_audit=True)

    with sqlite3.connect(source.config.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE id=?", (candidate.id,)
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
    _assert_absent_from_paths(source_dir, (CANARY.encode(),))
    assert CANARY.encode() not in package.read_bytes()


def test_supported_cli_migrates_pre_boundary_core_before_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "pre-boundary"
    source = CoreService(CoreConfig.in_directory(source_dir, require_auth=False))
    candidate = source.store.add_candidate(
        CandidateInput(kind="fact", content="Temporary synthetic row.")
    )
    with sqlite3.connect(source.config.database_path) as connection:
        connection.execute(
            "UPDATE context_candidates SET content=?,evidence=?,disposition='ignored' WHERE id=?",
            (SECRET_TEXT, SECRET_TEXT, candidate.id),
        )
        connection.execute("DROP TABLE secret_refusal_receipts")
        connection.execute("DELETE FROM schema_migrations WHERE version=8")
        connection.execute("UPDATE vaults SET schema_version=7")
        connection.commit()

    package = tmp_path / "migrated-before-export.atcexp"
    passphrase_env = "ATC_SYNTHETIC_EXPORT_PASSPHRASE"
    monkeypatch.setenv(passphrase_env, PASSPHRASE)
    cli._cmd_export(
        argparse.Namespace(
            data_dir=str(source_dir),
            destination=str(package),
            passphrase_env=passphrase_env,
            include_sources=False,
            include_audit=True,
        )
    )

    with sqlite3.connect(source.config.database_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=8").fetchone()[
                0
            ]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE id=?", (candidate.id,)
            ).fetchone()[0]
            == 0
        )
    _assert_absent_from_paths(source_dir, (CANARY.encode(),))
    assert CANARY.encode() not in package.read_bytes()


def test_relay_refuses_direct_secret_before_encrypted_queue_or_hash(
    tmp_path: Path,
) -> None:
    database = tmp_path / "relay.sqlite3"
    service = RelayService(
        SQLiteRelayStore(database),
        b"synthetic-relay-boundary-key-at-least-32-bytes",
    )
    identity = ClientIdentity(
        client_id="synthetic-client",
        vault_id="synthetic-vault",
        permissions=frozenset({"proposal:write"}),
        context_scopes=frozenset({"*"}),
    )
    try:
        with pytest.raises(SecretLikeProposalRefused):
            service.propose(
                identity,
                idempotency_key=hashlib.sha256(SECRET_TEXT.encode()).hexdigest(),
                proposal={
                    "kind": "fact",
                    "content": SECRET_TEXT,
                    "scope": ["synthetic"],
                },
            )
        assert service.queued_proposals(identity.vault_id) == []
    finally:
        service.close()

    _assert_absent_from_paths(
        tmp_path,
        (
            CANARY.encode(),
            hashlib.sha256(SECRET_TEXT.encode()).hexdigest().encode(),
        ),
    )
