"""Focused deterministic acceptance for the Continuous Capture foundation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from allthecontext.capture import (
    BackoffPolicy,
    CaptureCoordinator,
    CaptureError,
    CaptureEvent,
    CapturePage,
    DeterministicFakeAdapter,
    IdempotentFakeSink,
)
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.models import ClientCreate
from allthecontext.storage import CoreStore
from fastapi.testclient import TestClient


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.sqlite3")
    store.initialize_vault()
    return store


def _coordinator(tmp_path: Path, *, sink: IdempotentFakeSink | None = None) -> CaptureCoordinator:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=sink or IdempotentFakeSink())
    return coordinator


def _source(coordinator: CaptureCoordinator, *, acknowledged: bool = True) -> str:
    source = coordinator.create_source(
        provider="fake",
        account_label="synthetic-account",
        account_fingerprint="fingerprint-1",
        requested_scopes=("items.read",),
        local_only_acknowledged=acknowledged,
    )
    return source.id


def _event(
    event_id: str,
    item_id: str,
    order: str,
    *,
    operation: str = "upsert",
    payload: dict[str, Any] | None = None,
) -> CaptureEvent:
    return CaptureEvent(
        provider_event_id=event_id,
        provider_item_id=item_id,
        order_key=order,
        operation=operation,  # type: ignore[arg-type]
        payload=payload or {},
        generation=1,
    )


def _enable(coordinator: CaptureCoordinator, source_id: str) -> None:
    coordinator.enable(source_id)


def test_migration_015_restart_and_partial_damage_repair(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.connect() as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 15
        for table in (
            "capture_sources",
            "capture_checkpoints",
            "capture_events",
            "capture_items",
            "capture_runs",
        ):
            assert (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                is not None
            )
    with store.transaction() as connection:
        connection.execute("DROP TABLE capture_items")
    assert store.migrate() == 15
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_items'"
            ).fetchone()
            is not None
        )
    restarted = CoreStore(store.database_path)
    assert restarted.migrate() == 15


def test_default_disabled_local_only_lifecycle_and_invalid_transitions(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    source_id = _source(coordinator, acknowledged=False)
    assert coordinator.get_source(source_id).lifecycle_state == "disabled"
    with pytest.raises(CaptureError, match="capture_local_only_required"):
        coordinator.enable(source_id)

    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    assert coordinator.pause(source_id).lifecycle_state == "paused"
    assert coordinator.resume(source_id).lifecycle_state == "enabled"
    assert coordinator.disable(source_id).lifecycle_state == "disabled"
    assert coordinator.enable(source_id).lifecycle_state == "enabled"
    assert coordinator.revoke(source_id).lifecycle_state == "revoked"
    with pytest.raises(CaptureError, match="capture_invalid_transition"):
        coordinator.resume(source_id)


def test_disabled_paused_and_revoked_sources_make_zero_adapter_calls(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    source_id = _source(coordinator)
    adapter = DeterministicFakeAdapter(
        [CapturePage(generation=1, events=(_event("e1", "i1", "1"),))]
    )
    coordinator.register_adapter("fake", adapter)
    assert coordinator.run(source_id).error_code == "capture_source_not_enabled"
    _enable(coordinator, source_id)
    coordinator.pause(source_id)
    assert coordinator.run(source_id).error_code == "capture_source_not_enabled"
    coordinator.resume(source_id)
    coordinator.revoke(source_id)
    assert coordinator.run(source_id).error_code == "capture_source_not_enabled"
    assert adapter.calls == []


def test_ordered_pages_updates_deletes_duplicate_replay_and_stable_lineage(tmp_path: Path) -> None:
    sink = IdempotentFakeSink()
    coordinator = _coordinator(tmp_path, sink=sink)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    pages = [
        CapturePage(
            generation=1,
            page_order=0,
            events=(_event("e1", "item-1", "1", payload={"title": "first"}),),
            next_cursor="cursor-1",
            done=False,
        ),
        CapturePage(
            generation=1,
            page_order=1,
            events=(_event("e2", "item-1", "2", payload={"title": "updated"}),),
            next_cursor="cursor-2",
            done=False,
        ),
        CapturePage(
            generation=1,
            page_order=2,
            events=(_event("e3", "item-1", "3", operation="delete"),),
            done=True,
        ),
    ]
    adapter = DeterministicFakeAdapter(pages)
    coordinator.register_adapter("fake", adapter)
    first = coordinator.run(source_id)
    assert first.status == "completed"
    assert first.applied_events == 3
    assert len(sink.calls) == 3
    assert len({call[1] for call in sink.calls}) == 1
    with coordinator.ledger.store.connect() as connection:
        item = connection.execute(
            "SELECT canonical_record_id,item_state FROM capture_items "
            "WHERE source_id=? AND provider_item_id=?",
            (source_id, "item-1"),
        ).fetchone()
        event = connection.execute(
            "SELECT status,normalized_payload_json FROM capture_events "
            "WHERE source_id=? AND provider_event_id='e2'",
            (source_id,),
        ).fetchone()
    assert item is not None and item["item_state"] == "deleted"
    assert event is not None and json.loads(event["normalized_payload_json"]) == {
        "title": "updated"
    }

    replay = DeterministicFakeAdapter(pages)
    coordinator.register_adapter("fake", replay)
    second = coordinator.run(source_id)
    assert second.status == "completed"
    assert second.duplicate_events == 3
    assert len(sink.calls) == 3


def test_changed_provider_event_payload_is_rejected_without_overwrite(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    source_id = _source(coordinator)
    coordinator.ledger.stage_event(source_id, _event("e1", "i1", "1", payload={"a": "one"}))
    with pytest.raises(CaptureError, match="capture_event_payload_conflict"):
        coordinator.ledger.stage_event(source_id, _event("e1", "i1", "1", payload={"a": "two"}))
    with coordinator.ledger.store.connect() as connection:
        row = connection.execute(
            "SELECT normalized_payload_json FROM capture_events "
            "WHERE source_id=? AND provider_event_id='e1'",
            (source_id,),
        ).fetchone()
    assert row is not None and json.loads(row["normalized_payload_json"]) == {"a": "one"}


def test_out_of_order_malformed_and_oversize_inputs_degrade_without_checkpoint_advance(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    out_of_order = CapturePage(
        generation=1,
        events=(_event("e2", "i2", "2"), _event("e1", "i1", "1")),
    )
    coordinator.register_adapter("fake", DeterministicFakeAdapter([out_of_order]))
    result = coordinator.run(source_id)
    assert result.error_code == "capture_event_out_of_order"
    assert coordinator.ledger._checkpoint(source_id)["last_order_key"] is None

    coordinator.resume(source_id)
    oversize = CapturePage(
        generation=1,
        events=(_event("e-big", "i-big", "1", payload={"value": "x" * 2_001}),),
    )
    coordinator.register_adapter("fake", DeterministicFakeAdapter([oversize]))
    assert coordinator.run(source_id).error_code == "capture_payload_rejected"

    coordinator.resume(source_id)

    class MalformedAdapter:
        def fetch_page(self, source: Any, cursor: str | None, page_order: int) -> Any:
            del source, cursor, page_order
            return {"not": "a CapturePage"}

    coordinator.register_adapter("fake", MalformedAdapter())  # type: ignore[arg-type]
    assert coordinator.run(source_id).error_code == "capture_page_malformed"


def test_crash_after_sink_apply_before_commit_replays_idempotently(tmp_path: Path) -> None:
    sink = IdempotentFakeSink()
    sink.fail_once_after_apply = True
    coordinator = _coordinator(tmp_path, sink=sink)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    page = CapturePage(generation=1, events=(_event("e1", "i1", "1", payload={"v": 1}),))
    coordinator.register_adapter("fake", DeterministicFakeAdapter([page]))
    failed = coordinator.run(source_id)
    assert failed.status == "failed" and failed.error_code == "capture_sink_failed"
    assert coordinator.get_source(source_id).lifecycle_state == "degraded"
    coordinator.resume(source_id)
    coordinator.register_adapter("fake", DeterministicFakeAdapter([page]))
    replay = coordinator.run(source_id)
    assert replay.status == "completed"
    assert len(sink.calls) == 1
    with coordinator.ledger.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT status FROM capture_events WHERE source_id=? AND provider_event_id='e1'",
                (source_id,),
            ).fetchone()[0]
            == "applied"
        )


def test_gap_invalid_cursor_page_limit_backoff_and_lease_recovery(tmp_path: Path) -> None:
    assert BackoffPolicy(base_seconds=2, max_seconds=5).delay_seconds(3) == 5
    coordinator = _coordinator(tmp_path)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    gap_pages = [
        CapturePage(
            generation=1,
            page_order=0,
            events=(_event("e1", "i1", "1"),),
            done=False,
            next_cursor="c1",
        ),
        CapturePage(generation=1, page_order=1, events=(_event("e3", "i3", "3"),), done=True),
    ]
    coordinator.register_adapter("fake", DeterministicFakeAdapter(gap_pages))
    result = coordinator.run(source_id)
    assert result.error_code == "capture_event_gap"
    assert coordinator.get_source(source_id).next_retry_at is not None
    with pytest.raises(CaptureError, match="capture_invalid_cursor"):
        CapturePage(generation=1, done=False)

    coordinator.resume(source_id)
    pages = [
        CapturePage(generation=1, page_order=index, events=(), done=False, next_cursor=f"c{index}")
        for index in range(100)
    ]
    coordinator.register_adapter("fake", DeterministicFakeAdapter(pages))
    limited = coordinator.run(source_id)
    assert limited.error_code == "capture_page_limit_exceeded"

    with coordinator.ledger.store.transaction() as connection:
        now = "2000-01-01T00:00:00.000000Z"
        connection.execute(
            "INSERT INTO capture_runs(id,source_id,state,lease_token,lease_expires_at,started_at) "
            "VALUES('run-expired',?,'running','opaque','1999-01-01T00:00:00.000000Z',?)",
            (source_id, now),
        )
    assert coordinator.ledger.recover_expired_runs() == 1
    assert coordinator.get_source(source_id).last_error_code == "capture_lease_expired"


def test_secret_markers_never_enter_capture_sqlite_state(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    with pytest.raises(CaptureError, match="capture_payload_rejected"):
        coordinator.create_source(provider="fake", account_label="Bearer secret=do-not-store")
    source_id = _source(coordinator)
    with pytest.raises(CaptureError, match="capture_payload_rejected"):
        coordinator.ledger.stage_event(
            source_id,
            _event("e1", "i1", "1", payload={"api_token": "not-persisted"}),
        )
    with coordinator.ledger.store.connect() as connection:
        dump = " ".join(
            str(row[0])
            for table in ("capture_sources", "capture_events")
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
        )
    assert "do-not-store" not in dump and "not-persisted" not in dump


def test_capture_api_is_authenticated_and_content_free(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path / "api", require_auth=True)
    service = CoreService(config)
    principal, token = service.store.create_client(
        ClientCreate(name="capture-admin", scopes=["admin"], auto_approve=False)
    )
    assert principal.id
    app = create_app(config, service=service)
    with TestClient(app) as client:
        assert client.get("/v1/admin/capture/sources").status_code == 401
        response = client.post(
            "/v1/admin/capture/sources",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "provider": "fake",
                "account_label": "api-account",
                "local_only_acknowledged": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "credential_ref" not in response.text
        source_id = body["id"]
        enabled = client.post(
            f"/v1/admin/capture/sources/{source_id}/enable",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert enabled.status_code == 200
        unavailable = client.post(
            f"/v1/admin/capture/sources/{source_id}/run",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert unavailable.status_code == 200
        assert unavailable.json()["error_code"] == "capture_adapter_unavailable"
        assert "cursor" not in unavailable.text
        assert "payload" not in unavailable.text


def test_cli_capture_commands_are_content_free(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.close()
    from allthecontext.cli import main

    original = sys.argv
    try:
        sys.argv = ["atc", "capture", "create", "--data-dir", str(tmp_path), "fake", "cli-account"]
        main()
        output = capsys.readouterr().out
        assert "credential_ref" not in output
        assert "cursor" not in output
        assert "payload" not in output
    finally:
        sys.argv = original
