"""Focused proof for the bounded Core-owned registered-source seam."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from allthecontext import export as portable_export
from allthecontext.capture import (
    CaptureCoordinator,
    CaptureError,
    CaptureEvent,
    CaptureRunHandle,
    _canonical_lineage,
    _idempotency_key,
)
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.export import create_export, restore_export
from allthecontext.memory_policy import (
    REGISTERED_SOURCE_CODE_OWNED_SCOPES,
    AutomaticMemoryPolicy,
    ObservationOrigin,
    is_registered_source_fact,
    registered_source_reference,
)
from allthecontext.models import Availability, CandidateInput, ObservationDisposition, Sensitivity
from allthecontext.registered_source_admission import RegisteredSourceCaptureApplicationSink
from allthecontext.storage import CoreStore, NotFoundError

from tests.fixtures.local_git_workspace import create_sanitized_workspace


def _store(path: Path) -> CoreStore:
    store = CoreStore(path / "core.sqlite3")
    store.initialize_vault()
    return store


def _run(tmp_path: Path) -> tuple[CoreStore, CaptureCoordinator, Path, str]:
    store = _store(tmp_path)
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = LocalGitWorkspaceCaptureProviderAdapter((root,))
    sink = RegisteredSourceCaptureApplicationSink(store)
    coordinator = CaptureCoordinator(store, sink=sink)
    source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="sanitized-workspace",
        account_fingerprint=adapter.source_identity,
        requested_scopes=REGISTERED_SOURCE_CODE_OWNED_SCOPES,
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
    coordinator.enable(source.id)
    return store, coordinator, root, source.id


def test_registered_source_happy_path_uses_core_lineage_and_safe_projection(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    result = coordinator.run(source_id)
    assert result.status == "completed"
    assert result.applied_events == 4

    with store.connect() as connection:
        candidates = connection.execute(
            "SELECT * FROM context_candidates WHERE capture_source_id=? ORDER BY id",
            (source_id,),
        ).fetchall()
        records = connection.execute(
            "SELECT r.* FROM context_records r JOIN context_candidates c "
            "ON c.id=r.candidate_id WHERE c.capture_source_id=? ORDER BY r.id",
            (source_id,),
        ).fetchall()
        items = connection.execute(
            "SELECT provider_item_id,canonical_record_id FROM capture_items "
            "WHERE source_id=? ORDER BY provider_item_id",
            (source_id,),
        ).fetchall()
    assert len(candidates) == len(records) == len(items) == 4
    assert {str(row["observation_origin"]) for row in candidates} == {"registered_source"}
    assert {bool(row["explicit_user_statement"]) for row in candidates} == {False}
    assert {str(row["observation_origin"]) for row in records} == {"registered_source"}
    assert {bool(row["explicit_user_statement"]) for row in records} == {False}
    assert {str(row["source_type"]) for row in records} == {"registered_capture"}
    assert {row["source_id"] for row in records} == {None}
    assert {tuple(json.loads(str(row["scopes_json"]))) for row in records} == {
        ("workspace.structure",)
    }
    assert {str(row["id"]) for row in records} == {str(row["canonical_record_id"]) for row in items}


def test_registered_source_item_update_advances_candidate_without_duplicate_record(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        before = connection.execute(
            "SELECT r.id,r.candidate_id FROM context_records r "
            "JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
    assert before is not None

    (root / "README.md").write_text("# Updated fixture\n", encoding="utf-8", newline="\n")
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        after = connection.execute(
            "SELECT r.id,r.candidate_id,c.capture_event_id "
            "FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
        latest_candidate = (
            connection.execute(
                "SELECT id FROM context_candidates WHERE capture_event_id=?",
                (after["capture_event_id"],),
            ).fetchone()
            if after is not None
            else None
        )
        record_count = connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
    assert after is not None
    assert after["id"] == before["id"]
    assert after["candidate_id"] != before["candidate_id"]
    assert latest_candidate is not None
    assert after["candidate_id"] == latest_candidate["id"]
    assert record_count == 4


def test_registered_source_never_promotes_workspace_path_text_root_or_fingerprint(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    result = coordinator.run(source_id)
    assert result.status == "completed"
    forbidden = {
        "src/app.py",
        "def answer()",
        "fixture",
        "workspace-source-",
        str(root),
    }
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT content,structured_value_json,evidence FROM context_candidates "
            "UNION ALL SELECT content,structured_value_json,evidence FROM context_records"
        ).fetchall()
        audits = connection.execute("SELECT metadata_json FROM audit_events").fetchall()
    serialized = " ".join(repr(tuple(row)) for row in rows + audits)
    assert not any(value in serialized for value in forbidden)


def test_registered_source_payloads_are_metadata_only_for_admitted_and_no_fact_files(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    private_fixture = "PRIVATE_JSON_SECRET_FIXTURE"
    source_fixture = "SOURCE_SECRET_FIXTURE"
    (root / "private.json").write_text(
        '{"fixture": "PRIVATE_JSON_SECRET_FIXTURE"}\n',
        encoding="utf-8",
        newline="\n",
    )
    (root / "src/app.py").write_text(
        f"SOURCE_SECRET_FIXTURE = {source_fixture!r}\n",
        encoding="utf-8",
        newline="\n",
    )

    result = coordinator.run(source_id)
    assert result.status == "completed"
    assert result.applied_events == 5

    with store.connect() as connection:
        events = connection.execute(
            "SELECT normalized_payload_json,application_receipt "
            "FROM capture_events WHERE source_id=? ORDER BY id",
            (source_id,),
        ).fetchall()
        records = connection.execute(
            "SELECT content,structured_value_json,evidence FROM context_candidates "
            "UNION ALL SELECT content,structured_value_json,evidence FROM context_records"
        ).fetchall()
    payloads = [json.loads(str(row["normalized_payload_json"])) for row in events]
    upserts = [payload for payload in payloads if payload]
    assert len(events) == len(payloads) == 5
    assert all("text" not in payload for payload in upserts)
    assert all(
        set(payload)
        == {
            "relative_path",
            "root_id",
            "kind",
            "size",
            "content_sha256",
            "content_truncated",
            "hash_scope",
        }
        for payload in upserts
    )
    private_event = next(
        (
            row
            for row, payload in zip(events, payloads, strict=True)
            if payload["relative_path"] == "private.json"
        ),
        None,
    )
    assert private_event is not None
    assert private_event["application_receipt"] == "registered-source-no-fact"
    serialized = " ".join(repr(tuple(row)) for row in events + records)
    assert private_fixture not in serialized
    assert source_fixture not in serialized
    assert len(records) == 8


def test_registered_source_raw_provider_item_id_stays_in_capture_ledger_only(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    handle, _source, _attempt = coordinator.ledger.begin_run(source_id)
    raw_item_id = "C:/Users/Alice/secret.py"
    event = CaptureEvent(
        provider_event_id="path-like-item-event",
        provider_item_id=raw_item_id,
        order_key="g00000000000000000001-e00000001",
        generation=1,
        payload={
            "relative_path": "safe.py",
            "root_id": "opaque-root",
            "kind": "text",
            "size": 1,
            "content_sha256": "0" * 64,
            "content_truncated": False,
            "hash_scope": "full",
        },
    )
    event_id, _duplicate, _attempts = coordinator.ledger.stage_event(handle, event)
    sink = coordinator.sink
    assert sink is not None
    receipt = sink.apply(
        event,
        source_id=source_id,
        event_id=event_id,
        run_handle=handle,
        canonical_record_id=_canonical_lineage(source_id, raw_item_id),
        idempotency_key=_idempotency_key(source_id, event.provider_event_id),
    )
    coordinator.ledger.commit_event(
        handle=handle,
        event=event,
        event_id=event_id,
        receipt=receipt.receipt,
        canonical_record_id=receipt.canonical_record_id,
    )

    with store.connect() as connection:
        projection_rows = connection.execute(
            "SELECT source_reference,content,evidence FROM context_candidates "
            "UNION ALL SELECT source_reference,content,evidence FROM context_records"
        ).fetchall()
        version_rows = connection.execute(
            "SELECT snapshot_json FROM context_record_versions"
        ).fetchall()
        audit_rows = connection.execute("SELECT metadata_json FROM audit_events").fetchall()
        error_rows = connection.execute("SELECT * FROM context_errors").fetchall()
        stored_receipt = connection.execute(
            "SELECT application_receipt FROM capture_events WHERE id=?", (event_id,)
        ).fetchone()
    assert receipt.receipt.startswith("registered-source-fact:")
    assert stored_receipt is not None
    projection_text = repr([tuple(row) for row in projection_rows])
    durable_text = " ".join(
        [
            projection_text,
            repr([row["snapshot_json"] for row in version_rows]),
            repr([row["metadata_json"] for row in audit_rows]),
            repr([tuple(row) for row in error_rows]),
            repr(stored_receipt["application_receipt"]),
            repr(receipt.receipt),
        ]
    )
    assert raw_item_id not in durable_text
    assert all(
        str(row["source_reference"]).startswith("registered-source-item-")
        for row in projection_rows
    )
    assert (
        registered_source_reference(source_id, raw_item_id)
        == projection_rows[0]["source_reference"]
    )

    package = tmp_path / "raw-item.atcexp"
    passphrase = "raw-item-test-passphrase"
    create_export(store.database_path, package, passphrase, include_sources=True)
    decrypted = tmp_path / "raw-item.zip"
    portable_export._decrypt_file(package, decrypted, passphrase)
    assert raw_item_id.encode("utf-8") not in decrypted.read_bytes()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("content", "password=must-not-enter"),
        ("evidence", "secret evidence"),
        ("source_service", "forged-provider"),
        ("source_id", "forged-source"),
        ("sensitivity", Sensitivity.HIGHLY_SENSITIVE),
        ("availability", Availability.LOCAL),
        ("tags", ["forged-tag"]),
        ("allowed_clients", ["forged-client"]),
        ("source_reference", "C:/Users/Alice/secret.py"),
        ("idempotency_key", "C:/Users/Alice/secret.py"),
        ("entity_key", "forged-entity"),
        ("supersedes", "forged-record"),
        ("expires_at", "2027-01-01T00:00:00.000000Z"),
        ("explicit_user_statement", True),
    ),
)
def test_registered_source_policy_rejects_caller_crafted_projection_fields(
    tmp_path: Path, field: str, value: Any
) -> None:
    store, coordinator, _root, _source_id = _run(tmp_path)
    assert coordinator.run(_source_id).status == "completed"
    with store.connect() as connection:
        candidate_id = connection.execute(
            "SELECT id FROM context_candidates WHERE capture_source_id=? LIMIT 1",
            (_source_id,),
        ).fetchone()["id"]
    observed = store.get_candidate(str(candidate_id))
    base = CandidateInput.model_validate(
        {name: observed.model_dump(mode="python")[name] for name in CandidateInput.model_fields}
    )
    updates = {field: value}
    if field == "entity_key":
        updates["attribute_key"] = "forged-attribute"
    forged = CandidateInput.model_validate({**base.model_dump(mode="python"), **updates})
    assert is_registered_source_fact(forged) is False
    decision = AutomaticMemoryPolicy().evaluate(
        forged,
        origin=ObservationOrigin.REGISTERED_SOURCE,
    )
    assert decision.disposition == ObservationDisposition.IGNORED


@pytest.mark.parametrize(
    "scopes",
    (
        [],
        ["secret.credentials"],
        [*REGISTERED_SOURCE_CODE_OWNED_SCOPES, "workspace.extra"],
        [*REGISTERED_SOURCE_CODE_OWNED_SCOPES, *REGISTERED_SOURCE_CODE_OWNED_SCOPES],
    ),
)
def test_registered_source_policy_requires_exact_code_owned_scopes(
    tmp_path: Path, scopes: list[str]
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        candidate_id = connection.execute(
            "SELECT id FROM context_candidates WHERE capture_source_id=? LIMIT 1",
            (source_id,),
        ).fetchone()["id"]
    observed = store.get_candidate(str(candidate_id))
    base = CandidateInput.model_validate(
        {name: observed.model_dump(mode="python")[name] for name in CandidateInput.model_fields}
    )
    forged = CandidateInput.model_validate({**base.model_dump(mode="python"), "scopes": scopes})
    assert is_registered_source_fact(forged) is False
    decision = AutomaticMemoryPolicy().evaluate(
        forged,
        origin=ObservationOrigin.REGISTERED_SOURCE,
    )
    assert decision.disposition == ObservationDisposition.IGNORED


def test_registered_source_policy_requires_observed_at_and_real_extractor_version(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        candidate_id = connection.execute(
            "SELECT id FROM context_candidates WHERE capture_source_id=? LIMIT 1",
            (source_id,),
        ).fetchone()["id"]
    observed = store.get_candidate(str(candidate_id))
    base = CandidateInput.model_validate(
        {name: observed.model_dump(mode="python")[name] for name in CandidateInput.model_fields}
    )
    missing_observed_at = base.model_copy(update={"observed_at": None})
    assert is_registered_source_fact(missing_observed_at) is False
    structured = dict(base.structured_value or {})
    structured["extractor_version"] = True
    boolean_extractor_version = base.model_copy(update={"structured_value": structured})
    assert is_registered_source_fact(boolean_extractor_version) is False


@pytest.mark.parametrize(
    "scopes",
    (
        [],
        ["secret.credentials"],
        [*REGISTERED_SOURCE_CODE_OWNED_SCOPES, "workspace.extra"],
        [*REGISTERED_SOURCE_CODE_OWNED_SCOPES, *REGISTERED_SOURCE_CODE_OWNED_SCOPES],
    ),
)
def test_registered_source_sink_rejects_malicious_source_scopes_content_free(
    tmp_path: Path, scopes: list[str]
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    handle, _source, _attempt = coordinator.ledger.begin_run(source_id)
    event = CaptureEvent(
        provider_event_id="scope-event",
        provider_item_id="scope-item",
        order_key="g00000000000000000001-e00000001",
        generation=1,
        payload={
            "relative_path": "safe.py",
            "kind": "text",
            "size": 1,
            "content_sha256": "0" * 64,
            "content_truncated": False,
            "hash_scope": "full",
        },
    )
    event_id, _duplicate, _attempts = coordinator.ledger.stage_event(handle, event)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE capture_sources SET requested_scopes_json=? WHERE id=?",
            (json.dumps(scopes), source_id),
        )
    sink = coordinator.sink
    assert sink is not None
    with pytest.raises(CaptureError) as error:
        sink.apply(
            event,
            source_id=source_id,
            event_id=event_id,
            run_handle=handle,
            canonical_record_id=_canonical_lineage(source_id, event.provider_item_id),
            idempotency_key=_idempotency_key(source_id, event.provider_event_id),
        )
    assert str(error.value) == "capture_sink_failed"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM context_candidates").fetchone()[0] == 0


def test_registered_source_crash_after_core_admission_replays_one_projection(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = LocalGitWorkspaceCaptureProviderAdapter((root,))
    real_sink = RegisteredSourceCaptureApplicationSink(store)

    class CrashAfterAdmission:
        def __init__(self) -> None:
            self.failed = False

        def apply(self, event: CaptureEvent, **kwargs: Any) -> Any:
            receipt = real_sink.apply(event, **kwargs)
            if not self.failed:
                self.failed = True
                raise CaptureError("capture_sink_failed")
            return receipt

    coordinator = CaptureCoordinator(store, sink=CrashAfterAdmission())
    source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="sanitized-workspace",
        account_fingerprint=adapter.source_identity,
        requested_scopes=("workspace.structure",),
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
    coordinator.enable(source.id)
    first = coordinator.run(source.id)
    assert first.status == "failed"
    coordinator.resume(source.id)
    second = coordinator.run(source.id)
    assert second.status == "completed"
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE capture_source_id=?",
                (source.id,),
            ).fetchone()[0]
            == 4
        )
        assert connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 4


def test_registered_source_pending_recovery_withdraws_deleted_file_without_tombstone(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = LocalGitWorkspaceCaptureProviderAdapter((root,))
    real_sink = RegisteredSourceCaptureApplicationSink(store)

    class CrashAfterAdmission:
        def __init__(self) -> None:
            self.failed = False

        def apply(self, event: CaptureEvent, **kwargs: Any) -> Any:
            receipt = real_sink.apply(event, **kwargs)
            if not self.failed:
                self.failed = True
                raise CaptureError("capture_sink_failed")
            return receipt

    coordinator = CaptureCoordinator(store, sink=CrashAfterAdmission())
    source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="sanitized-workspace",
        account_fingerprint=adapter.source_identity,
        requested_scopes=("workspace.structure",),
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
    coordinator.enable(source.id)
    assert coordinator.run(source.id).status == "failed"
    with store.connect() as connection:
        admitted = connection.execute(
            "SELECT r.id,json_extract(e.normalized_payload_json,'$.relative_path') AS "
            "relative_path "
            "FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id WHERE c.capture_source_id=? LIMIT 1",
            (source.id,),
        ).fetchone()
        pending = connection.execute(
            "SELECT pending_generation,pending_event_ids_json "
            "FROM capture_checkpoints WHERE source_id=?",
            (source.id,),
        ).fetchone()
    assert admitted is not None
    assert pending is not None and pending["pending_generation"] == 1
    record_id = str(admitted["id"])

    relative_path = admitted["relative_path"]
    assert isinstance(relative_path, str)
    (root / Path(relative_path)).unlink()
    coordinator.resume(source.id)
    recovered = coordinator.run(source.id)
    assert recovered.status == "completed"
    with pytest.raises(NotFoundError):
        store.get_record(record_id)
    with store.connect() as connection:
        item = connection.execute(
            "SELECT item_state FROM capture_items WHERE source_id=? AND provider_item_id IN "
            "(SELECT provider_item_id FROM capture_events "
            "WHERE source_id=? AND operation='delete')",
            (source.id, source.id),
        ).fetchone()
        tombstone = connection.execute(
            "SELECT 1 FROM deletion_tombstones WHERE record_id=?", (record_id,)
        ).fetchone()
        checkpoint = connection.execute(
            "SELECT pending_generation,pending_cursor,pending_event_ids_json "
            "FROM capture_checkpoints "
            "WHERE source_id=?",
            (source.id,),
        ).fetchone()
    assert item is None or item["item_state"] == "deleted"
    assert tombstone is None
    assert checkpoint is not None and tuple(checkpoint) == (None, None, None)


def test_registered_source_scrubbed_purge_replays_sink_and_commit_event(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    original_commit_page_cursor = coordinator.ledger.commit_page_cursor
    crashed = False

    def crash_before_cursor(*_args: Any, **_kwargs: Any) -> None:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise CaptureError("capture_failed")
        original_commit_page_cursor(*_args, **_kwargs)

    coordinator.ledger.commit_page_cursor = crash_before_cursor  # type: ignore[method-assign]
    assert coordinator.run(source_id).status == "failed"
    coordinator.ledger.commit_page_cursor = original_commit_page_cursor  # type: ignore[method-assign]
    with store.connect() as connection:
        target = connection.execute(
            "SELECT r.id,e.id AS event_id FROM context_records r "
            "JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id WHERE c.capture_source_id=? LIMIT 1",
            (source_id,),
        ).fetchone()
    assert target is not None
    record_id = str(target["id"])
    event_id = str(target["event_id"])
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )

    coordinator.resume(source_id)
    recovered = coordinator.run(source_id)
    assert recovered.status == "completed"
    with store.connect() as connection:
        scrubbed = connection.execute(
            "SELECT status,normalized_payload_json FROM capture_events WHERE id=?",
            (event_id,),
        ).fetchone()
        checkpoint = connection.execute(
            "SELECT pending_generation,pending_cursor,pending_event_ids_json "
            "FROM capture_checkpoints "
            "WHERE source_id=?",
            (source_id,),
        ).fetchone()
    assert scrubbed is not None and tuple(scrubbed) == ("applied", "{}")
    assert checkpoint is not None and tuple(checkpoint) == (None, None, None)
    with pytest.raises(NotFoundError):
        store.get_record(record_id)


def test_registered_source_staged_replay_after_correction_is_no_influence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = LocalGitWorkspaceCaptureProviderAdapter((root,))
    real_sink = RegisteredSourceCaptureApplicationSink(store)

    class CrashAfterAdmission:
        def __init__(self) -> None:
            self.failed = False

        def apply(self, event: CaptureEvent, **kwargs: Any) -> Any:
            receipt = real_sink.apply(event, **kwargs)
            if not self.failed:
                self.failed = True
                raise CaptureError("capture_sink_failed")
            return receipt

    coordinator = CaptureCoordinator(store, sink=CrashAfterAdmission())
    source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="sanitized-workspace",
        account_fingerprint=adapter.source_identity,
        requested_scopes=("workspace.structure",),
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
    coordinator.enable(source.id)
    assert coordinator.run(source.id).status == "failed"
    with store.connect() as connection:
        admitted = connection.execute(
            "SELECT r.id,c.id AS candidate_id,e.id AS event_id "
            "FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id LIMIT 1"
        ).fetchone()
    assert admitted is not None
    corrected = store.correct_record(
        str(admitted["id"]),
        content="User correction wins before capture commit.",
        reason="staged replay barrier fixture",
    )

    coordinator.resume(source.id)
    assert coordinator.run(source.id).status == "completed"
    assert store.get_record(str(admitted["id"])).content == corrected.content
    with store.connect() as connection:
        replayed = connection.execute(
            "SELECT c.disposition,e.application_receipt FROM context_candidates c "
            "JOIN capture_events e ON e.id=c.capture_event_id WHERE c.id=?",
            (admitted["candidate_id"],),
        ).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 4
    assert replayed is not None
    assert replayed["disposition"] == ObservationDisposition.IGNORED.value
    assert replayed["application_receipt"] == "registered-source-no-influence"


def test_registered_source_delete_withdraws_and_reupsert_revives_exact_id(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        item = connection.execute(
            "SELECT i.provider_item_id,i.canonical_record_id "
            "FROM capture_items i JOIN capture_events e ON e.id=i.last_event_id "
            "WHERE i.source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
    assert item is not None
    target_id = str(item["canonical_record_id"])
    (root / "README.md").unlink()
    deleted = coordinator.run(source_id)
    assert deleted.status == "completed"
    with pytest.raises(NotFoundError):
        store.get_record(target_id)
    with store.connect() as connection:
        tombstone = connection.execute(
            "SELECT 1 FROM deletion_tombstones WHERE record_id=?", (target_id,)
        ).fetchone()
        deleted_row = connection.execute(
            "SELECT deleted_at FROM context_records WHERE id=?", (target_id,)
        ).fetchone()
    assert tombstone is None
    assert deleted_row is not None and deleted_row["deleted_at"] is not None
    (root / "README.md").write_text("# Reappeared fixture\n", encoding="utf-8")
    revived = coordinator.run(source_id)
    assert revived.status == "completed"
    assert store.get_record(target_id).id == target_id


def test_registered_source_text_to_binary_withdraws_exact_record_and_replays(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        target = connection.execute(
            "SELECT r.id FROM context_records r "
            "JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
    assert target is not None
    record_id = str(target["id"])
    (root / "README.md").write_bytes(b"\x80\x81\x82")

    real_sink = coordinator.sink
    assert real_sink is not None

    class CrashAfterNoFact:
        def __init__(self) -> None:
            self.failed = False

        def apply(self, event: CaptureEvent, **kwargs: Any) -> Any:
            receipt = real_sink.apply(event, **kwargs)
            if not self.failed and receipt.receipt == "registered-source-no-fact":
                self.failed = True
                raise CaptureError("capture_sink_failed")
            return receipt

    coordinator.sink = CrashAfterNoFact()  # type: ignore[assignment]
    failed = coordinator.run(source_id)
    assert failed.status == "failed"
    with store.connect() as connection:
        withdrawn = connection.execute(
            "SELECT deleted_at FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        tombstone = connection.execute(
            "SELECT 1 FROM deletion_tombstones WHERE record_id=?", (record_id,)
        ).fetchone()
    assert withdrawn is not None and withdrawn["deleted_at"] is not None
    assert tombstone is None

    coordinator.resume(source_id)
    replayed = coordinator.run(source_id)
    assert replayed.status == "completed"
    with store.connect() as connection:
        event = connection.execute(
            "SELECT e.application_receipt,c.id AS candidate_id "
            "FROM capture_events e LEFT JOIN context_candidates c ON c.capture_event_id=e.id "
            "WHERE e.source_id=? AND e.generation=2 "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
        candidate_count = connection.execute(
            "SELECT COUNT(*) FROM context_candidates WHERE capture_source_id=?",
            (source_id,),
        ).fetchone()[0]
        item = connection.execute(
            "SELECT item_state FROM capture_items WHERE source_id=? AND canonical_record_id=?",
            (source_id, record_id),
        ).fetchone()
    assert event is not None
    assert event["application_receipt"] == "registered-source-no-fact"
    assert event["candidate_id"] is None
    assert candidate_count == 4
    assert item is not None and item["item_state"] == "active"


def test_registered_source_no_fact_does_not_cross_user_barriers(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    record_ids: dict[str, str] = {}
    for relative_path in ("README.md", "docs/decision.md", "src/app.py"):
        with store.connect() as connection:
            target = connection.execute(
                "SELECT r.id FROM context_records r "
                "JOIN context_candidates c ON c.id=r.candidate_id "
                "JOIN capture_events e ON e.id=c.capture_event_id "
                "WHERE c.capture_source_id=? "
                "AND json_extract(e.normalized_payload_json,'$.relative_path')=?",
                (source_id, relative_path),
            ).fetchone()
        assert target is not None
        record_ids[relative_path] = str(target["id"])

    corrected = store.correct_record(
        record_ids["README.md"],
        content="User correction remains authoritative.",
        reason="no-fact correction barrier fixture",
    )
    local = store.change_availability(record_ids["docs/decision.md"], Availability.LOCAL)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE context_records SET source_type='unlinked-source' WHERE id=?",
            (record_ids["src/app.py"],),
        )

    for relative_path in ("README.md", "docs/decision.md", "src/app.py"):
        (root / Path(relative_path)).write_bytes(b"\x80\x81\x82")
    result = coordinator.run(source_id)
    assert result.status == "completed"
    with store.connect() as connection:
        receipts = connection.execute(
            "SELECT e.application_receipt FROM capture_events e "
            "WHERE e.source_id=? AND e.generation=2 ORDER BY e.id",
            (source_id,),
        ).fetchall()
        candidate_count = connection.execute(
            "SELECT COUNT(*) FROM context_candidates WHERE capture_source_id=?",
            (source_id,),
        ).fetchone()[0]
        unlinked = connection.execute(
            "SELECT source_type FROM context_records WHERE id=?",
            (record_ids["src/app.py"],),
        ).fetchone()
    assert len(receipts) == 3
    assert {row["application_receipt"] for row in receipts} == {"registered-source-no-fact"}
    assert candidate_count == 4
    assert store.get_record(record_ids["README.md"]).content == corrected.content
    current = store.get_record(record_ids["docs/decision.md"])
    assert current.availability == Availability.LOCAL
    assert current.version == local.version
    assert unlinked is not None and unlinked["source_type"] == "unlinked-source"


def test_registered_source_user_mutation_and_delete_barriers_win(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        target = connection.execute(
            "SELECT r.id FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
    assert target is not None
    record_id = str(target["id"])

    corrected = store.correct_record(
        record_id,
        content="User correction remains authoritative.",
        reason="focused fixture correction",
    )
    (root / "README.md").write_text("# Changed fixture\n", encoding="utf-8")
    assert coordinator.run(source_id).status == "completed"
    assert store.get_record(record_id).content == corrected.content
    local = store.change_availability(record_id, Availability.LOCAL)
    (root / "README.md").write_text("# Changed again\n", encoding="utf-8")
    assert coordinator.run(source_id).status == "completed"
    current = store.get_record(record_id)
    assert current.availability == Availability.LOCAL
    assert current.version == local.version

    store.delete_record(record_id, reason="focused ordinary delete")
    (root / "README.md").write_text("# Changed after delete\n", encoding="utf-8")
    assert coordinator.run(source_id).status == "completed"
    with pytest.raises(NotFoundError):
        store.get_record(record_id)
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM deletion_tombstones WHERE record_id=?", (record_id,)
            ).fetchone()
            is not None
        )


def test_registered_source_unlinked_record_consumes_new_event_without_replacement(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        target = connection.execute(
            "SELECT r.id FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
    assert target is not None
    record_id = str(target["id"])
    with store.transaction() as connection:
        connection.execute(
            "UPDATE context_records SET source_type='unlinked-source' WHERE id=?",
            (record_id,),
        )
    previous = store.get_record(record_id)
    (root / "README.md").write_text("# Unlinked replacement\n", encoding="utf-8", newline="\n")
    assert coordinator.run(source_id).status == "completed"
    assert store.get_record(record_id).content == previous.content
    with store.connect() as connection:
        latest = connection.execute(
            "SELECT c.disposition,e.application_receipt FROM context_candidates c "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? ORDER BY c.created_at DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 4
    assert latest is not None
    assert latest["disposition"] == ObservationDisposition.IGNORED.value
    assert latest["application_receipt"] == "registered-source-no-influence"


def test_registered_source_delete_cannot_cross_source_item_boundaries(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path / "first")
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        first_item = connection.execute(
            "SELECT provider_item_id,canonical_record_id FROM capture_items "
            "WHERE source_id=? ORDER BY provider_item_id LIMIT 1",
            (source_id,),
        ).fetchone()
    assert first_item is not None
    first_record_id = str(first_item["canonical_record_id"])

    second_root = create_sanitized_workspace(tmp_path / "second" / "workspace")
    second_adapter = LocalGitWorkspaceCaptureProviderAdapter((second_root,))
    second_source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="second-sanitized-workspace",
        account_fingerprint=second_adapter.source_identity,
        requested_scopes=("workspace.structure",),
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, second_adapter)
    coordinator.enable(second_source.id)
    handle, _source, _attempt = coordinator.ledger.begin_run(second_source.id)
    event = CaptureEvent(
        provider_event_id="cross-source-delete",
        provider_item_id=str(first_item["provider_item_id"]),
        order_key="g00000000000000000001-e00000001",
        operation="delete",
        generation=1,
    )
    event_id, _duplicate, _attempts = coordinator.ledger.stage_event(handle, event)
    sink = coordinator.sink
    assert sink is not None
    receipt = sink.apply(
        event,
        source_id=second_source.id,
        event_id=event_id,
        run_handle=handle,
        canonical_record_id=_canonical_lineage(second_source.id, event.provider_item_id),
        idempotency_key=_idempotency_key(second_source.id, event.provider_event_id),
    )
    assert receipt.receipt == "registered-source-withdrawn"
    coordinator.ledger.commit_event(
        handle=handle,
        event=event,
        event_id=event_id,
        receipt=receipt.receipt,
        canonical_record_id=receipt.canonical_record_id,
    )
    assert store.get_record(first_record_id).id == first_record_id


def test_registered_source_export_is_portable_and_legacy_capture_rows_are_ignored(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    package = tmp_path / "portable.atcexp"
    passphrase = "registered-source-test-passphrase"
    manifest = create_export(store.database_path, package, passphrase, include_sources=True)
    assert not set(manifest["tables"]).intersection(
        {
            "capture_sources",
            "capture_events",
            "capture_items",
            "capture_checkpoints",
            "capture_runs",
        }
    )
    decrypted = tmp_path / "portable.zip"
    portable_export._decrypt_file(package, decrypted, passphrase)
    with zipfile.ZipFile(decrypted) as archive:
        candidate_rows = [
            json.loads(line)
            for line in archive.read("tables/context_candidates.jsonl").splitlines()
        ]
    assert candidate_rows
    assert all(row["capture_source_id"] is None for row in candidate_rows)
    assert all(row["capture_event_id"] is None for row in candidate_rows)
    assert all(len(str(row["capture_binding_hash"])) == 64 for row in candidate_rows)

    legacy_payload = tmp_path / "legacy-payload.zip"
    with (
        zipfile.ZipFile(decrypted) as incoming,
        zipfile.ZipFile(legacy_payload, "w", compression=zipfile.ZIP_DEFLATED) as outgoing,
    ):
        members = {info.filename: incoming.read(info.filename) for info in incoming.infolist()}
        candidate_name = "tables/context_candidates.jsonl"
        legacy_candidates = []
        for line in members[candidate_name].splitlines():
            row = json.loads(line)
            row["capture_source_id"] = "legacy-source"
            row["capture_event_id"] = "legacy-event"
            legacy_candidates.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
        members[candidate_name] = ("\n".join(legacy_candidates) + "\n").encode()
        legacy_names = {
            "tables/capture_sources.jsonl": b'{"id":"legacy-source"}\n',
            "tables/capture_events.jsonl": b'{"id":"legacy-event"}\n',
        }
        members.update(legacy_names)
        legacy_manifest = json.loads(members["manifest.json"])
        for name, content in legacy_names.items():
            table = name.removeprefix("tables/").removesuffix(".jsonl")
            legacy_manifest["tables"][table] = 1
            legacy_manifest["sha256"][name] = hashlib.sha256(content).hexdigest()
        legacy_manifest["sha256"][candidate_name] = hashlib.sha256(
            members[candidate_name]
        ).hexdigest()
        members["manifest.json"] = json.dumps(legacy_manifest, indent=2, sort_keys=True).encode()
        for name, content in members.items():
            outgoing.writestr(name, content)
    legacy_package = tmp_path / "legacy.atcexp"
    portable_export._encrypt_file(legacy_payload, legacy_package, passphrase)
    restored = _store(tmp_path / "restored")
    restore_export(legacy_package, restored.database_path, passphrase)
    with restored.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 0
        restored_candidate = connection.execute(
            "SELECT capture_source_id,capture_event_id FROM context_candidates LIMIT 1"
        ).fetchone()
        assert restored_candidate is not None
        assert restored_candidate["capture_source_id"] is None
        assert restored_candidate["capture_event_id"] is None


def test_registered_source_event_id_uniqueness_and_restart_retain_capture_state(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(context_candidates)")
        }
        assert {"capture_source_id", "capture_event_id", "capture_binding_hash"} <= columns
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='uq_context_candidates_capture_event'"
            ).fetchone()
            is not None
        )
        assert tuple(
            connection.execute(
                "SELECT COUNT(*),COUNT(DISTINCT capture_event_id) "
                "FROM context_candidates WHERE capture_event_id IS NOT NULL"
            ).fetchone()
        ) == (4, 4)
    restarted = CoreStore(store.database_path)
    assert restarted.migrate() == 17
    with restarted.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 4
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE capture_source_id=?",
                (source_id,),
            ).fetchone()[0]
            == 4
        )


def test_post_purge_registered_event_is_scrubbed_and_cannot_influence(
    tmp_path: Path,
) -> None:
    store, coordinator, root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        target = connection.execute(
            "SELECT r.id,c.id AS candidate_id,c.capture_event_id "
            "FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')='README.md'",
            (source_id,),
        ).fetchone()
    assert target is not None
    record_id = str(target["id"])
    event_id = str(target["capture_event_id"])
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT normalized_payload_json FROM capture_events WHERE id=?", (event_id,)
            ).fetchone()[0]
            == "{}"
        )
    (root / "README.md").write_text("# post purge\n", encoding="utf-8")
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_records WHERE id=?", (record_id,)
            ).fetchone()[0]
            == 0
        )


def test_registered_source_barrier_only_honors_record_purge_tombstones(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    assert coordinator.run(source_id).status == "completed"
    with store.connect() as connection:
        target = connection.execute(
            "SELECT r.id,r.vault_id,r.source_reference "
            "FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "WHERE c.capture_source_id=? LIMIT 1",
            (source_id,),
        ).fetchone()
    assert target is not None
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO purge_tombstones"
            "(stable_id,vault_id,target_type,purged_at) VALUES(?,?,?,?)",
            (target["id"], target["vault_id"], "source", "2026-08-23T00:00:00+00:00"),
        )
        assert (
            store._registered_source_influence_barrier_tx(
                connection,
                canonical_record_id=str(target["id"]),
                capture_source_id=source_id,
                source_reference=str(target["source_reference"]),
            )
            is None
        )


def test_registered_source_rejects_forged_sink_lineage_and_provider_payload_authority(
    tmp_path: Path,
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    handle, _source, _attempt = coordinator.ledger.begin_run(source_id)
    event = CaptureEvent(
        provider_event_id="forged-event",
        provider_item_id="forged-item",
        order_key="g00000000000000000001-e00000001",
        generation=1,
        payload={
            "relative_path": "safe.py",
            "root_id": "opaque-root",
            "kind": "text",
            "size": 1,
            "content_sha256": "0" * 64,
            "content_truncated": False,
            "hash_scope": "full",
            "project": "ignored",
            "authority": "ignored",
            "acl": ["ignored"],
            "text": "raw workspace text must not be used",
        },
    )
    event_id, _duplicate, _attempts = coordinator.ledger.stage_event(handle, event)
    with pytest.raises(CaptureError):
        coordinator.sink.apply(  # type: ignore[union-attr]
            event,
            source_id=source_id,
            event_id=event_id,
            run_handle=handle,
            canonical_record_id="forged-record",
            idempotency_key="forged-idempotency",
        )
    receipt = coordinator.sink.apply(  # type: ignore[union-attr]
        event,
        source_id=source_id,
        event_id=event_id,
        run_handle=handle,
        canonical_record_id=_canonical_lineage(source_id, event.provider_item_id),
        idempotency_key=_idempotency_key(source_id, event.provider_event_id),
    )
    assert receipt.receipt.startswith("registered-source-fact:")
    unknown = CaptureEvent(
        provider_event_id="unknown-class",
        provider_item_id="unknown-item",
        order_key="g00000000000000000001-e00000002",
        generation=1,
        payload={
            "relative_path": "image.bin",
            "kind": "binary",
            "size": 1,
            "content_sha256": "1" * 64,
            "content_truncated": False,
            "hash_scope": "full",
        },
    )
    unknown_id, _duplicate, _attempts = coordinator.ledger.stage_event(handle, unknown)
    no_fact = coordinator.sink.apply(  # type: ignore[union-attr]
        unknown,
        source_id=source_id,
        event_id=unknown_id,
        run_handle=handle,
        canonical_record_id=_canonical_lineage(source_id, unknown.provider_item_id),
        idempotency_key=_idempotency_key(source_id, unknown.provider_event_id),
    )
    assert no_fact.receipt == "registered-source-no-fact"
    coordinator.ledger.commit_event(
        handle=handle,
        event=unknown,
        event_id=unknown_id,
        receipt=no_fact.receipt,
        canonical_record_id=no_fact.canonical_record_id,
    )
    replayed_no_fact = coordinator.sink.apply(  # type: ignore[union-attr]
        unknown,
        source_id=source_id,
        event_id=unknown_id,
        run_handle=handle,
        canonical_record_id=_canonical_lineage(source_id, unknown.provider_item_id),
        idempotency_key=_idempotency_key(source_id, unknown.provider_event_id),
    )
    assert replayed_no_fact == no_fact
    unknown_text = CaptureEvent(
        provider_event_id="unknown-json",
        provider_item_id="unknown-json-item",
        order_key="g00000000000000000001-e00000003",
        generation=1,
        payload={
            "relative_path": "data.json",
            "kind": "text",
            "size": 1,
            "content_sha256": "2" * 64,
            "content_truncated": False,
            "hash_scope": "full",
        },
    )
    unknown_text_id, _duplicate, _attempts = coordinator.ledger.stage_event(handle, unknown_text)
    no_text_fact = coordinator.sink.apply(  # type: ignore[union-attr]
        unknown_text,
        source_id=source_id,
        event_id=unknown_text_id,
        run_handle=handle,
        canonical_record_id=_canonical_lineage(source_id, unknown_text.provider_item_id),
        idempotency_key=_idempotency_key(source_id, unknown_text.provider_event_id),
    )
    assert no_text_fact.receipt == "registered-source-no-fact"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM context_candidates").fetchone()[0] == 1


@pytest.mark.parametrize(
    "forgery",
    (
        "source",
        "event",
        "provider",
        "fingerprint",
        "item",
        "generation",
        "order",
        "operation",
        "payload",
        "idempotency",
        "lineage",
        "run",
        "lease",
    ),
)
def test_registered_source_exact_durable_projection_fails_closed(
    tmp_path: Path, forgery: str
) -> None:
    store, coordinator, _root, source_id = _run(tmp_path)
    handle, _source, _attempt = coordinator.ledger.begin_run(source_id)
    event = CaptureEvent(
        provider_event_id="exact-event",
        provider_item_id="exact-item",
        order_key="g00000000000000000001-e00000001",
        generation=1,
        payload={
            "relative_path": "safe.py",
            "kind": "text",
            "size": 1,
            "content_sha256": "0" * 64,
            "content_truncated": False,
            "hash_scope": "full",
        },
    )
    event_id, _duplicate, _attempts = coordinator.ledger.stage_event(handle, event)
    sink = coordinator.sink
    assert sink is not None
    source_argument = source_id
    event_argument = event_id
    event_argument_value = event
    handle_argument = handle
    canonical_argument = _canonical_lineage(source_id, event.provider_item_id)
    idempotency_argument = _idempotency_key(source_id, event.provider_event_id)
    if forgery == "source":
        source_argument = "forged-source"
    elif forgery == "event":
        event_argument = "forged-event"
    elif forgery == "provider":
        with store.transaction() as connection:
            connection.execute(
                "UPDATE capture_sources SET provider='forged-provider' WHERE id=?", (source_id,)
            )
    elif forgery == "fingerprint":
        with store.transaction() as connection:
            connection.execute(
                "UPDATE capture_sources SET account_fingerprint='forged-fingerprint' WHERE id=?",
                (source_id,),
            )
    elif forgery == "item":
        event_argument_value = replace(event, provider_item_id="forged-item")
    elif forgery == "generation":
        event_argument_value = replace(event, generation=2)
    elif forgery == "order":
        event_argument_value = replace(event, order_key="g00000000000000000001-e00000002")
    elif forgery == "operation":
        event_argument_value = replace(event, operation="delete", payload={})
    elif forgery == "payload":
        event_argument_value = replace(event, payload={**event.payload, "project": "forged"})
    elif forgery == "idempotency":
        idempotency_argument = "forged-idempotency"
    elif forgery == "lineage":
        canonical_argument = "forged-record"
    elif forgery == "run":
        handle_argument = CaptureRunHandle._mint("forged-run", source_id, handle.lease_token)
    elif forgery == "lease":
        handle_argument = CaptureRunHandle._mint(handle.run_id, source_id, "forged-lease")
    else:  # pragma: no cover - pytest supplies only the closed parameter set
        raise AssertionError(forgery)
    with pytest.raises(CaptureError):
        sink.apply(
            event_argument_value,
            source_id=source_argument,
            event_id=event_argument,
            run_handle=handle_argument,
            canonical_record_id=canonical_argument,
            idempotency_key=idempotency_argument,
        )
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM context_candidates").fetchone()[0] == 0
