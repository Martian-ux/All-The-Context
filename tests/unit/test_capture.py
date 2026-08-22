"""Focused deterministic acceptance for the Continuous Capture foundation."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from allthecontext.capture import (
    BackoffPolicy,
    CaptureApplicationReceipt,
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


def _begin(coordinator: CaptureCoordinator, source_id: str) -> Any:
    handle, _source, _attempt = coordinator.ledger.begin_run(source_id)
    return handle


def _capture_schema_snapshot(connection: Any) -> tuple[tuple[Any, ...], ...]:
    names = (
        "capture_sources",
        "ix_capture_sources_vault_state",
        "capture_checkpoints",
        "capture_events",
        "ix_capture_events_source_order",
        "ix_capture_events_source_status",
        "capture_items",
        "capture_runs",
        "ix_capture_runs_source_time",
        "ix_capture_runs_lease",
    )
    placeholders = ",".join("?" for _ in names)
    rows = connection.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name",
        names,
    ).fetchall()
    return tuple(tuple(row) for row in rows)


class _MutableClock:
    def __init__(self, value: str = "2026-01-01T00:00:00.000000Z") -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


class _GatedAdapter:
    def __init__(self, page: CapturePage, gate: Any) -> None:
        self.page = page
        self.gate = gate
        self.calls = 0

    def fetch_page(self, source: Any, cursor: str | None, page_order: int) -> CapturePage:
        del source, cursor, page_order
        self.calls += 1
        self.gate()
        return self.page


class _ExpiringSink(IdempotentFakeSink):
    def __init__(self, clock: _MutableClock) -> None:
        super().__init__()
        self.clock = clock
        self.expire_once = True

    def apply(
        self,
        event: CaptureEvent,
        *,
        source_id: str,
        canonical_record_id: str,
        idempotency_key: str,
    ) -> str:
        receipt = super().apply(
            event,
            source_id=source_id,
            canonical_record_id=canonical_record_id,
            idempotency_key=idempotency_key,
        )
        if self.expire_once:
            self.expire_once = False
            self.clock.value = "2026-01-01T00:01:01.000000Z"
        return receipt


def test_migration_015_repair_matches_canonical_schema_and_rejects_malformed_rows(
    tmp_path: Path,
) -> None:
    object_names = (
        "capture_sources",
        "capture_checkpoints",
        "capture_events",
        "capture_items",
        "capture_runs",
        "ix_capture_sources_vault_state",
        "ix_capture_events_source_order",
        "ix_capture_events_source_status",
        "ix_capture_runs_source_time",
        "ix_capture_runs_lease",
    )
    for object_name in object_names:
        candidate = _store(tmp_path / object_name)
        with candidate.connect() as connection:
            expected = _capture_schema_snapshot(connection)
        with candidate.transaction() as connection:
            kind = "INDEX" if object_name.startswith("ix_") else "TABLE"
            connection.execute(f'DROP {kind} IF EXISTS "{object_name}"')
            assert (
                connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 15
            )
        assert candidate.migrate() == 15
        with candidate.connect() as connection:
            assert _capture_schema_snapshot(connection) == expected

    coordinator = _coordinator(tmp_path / "malformed")
    source_id = _source(coordinator)
    vault_id = coordinator.ledger.store.vault_id()
    now = "2026-01-01T00:00:00.000000Z"
    with coordinator.ledger.store.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO capture_sources"
                "(id,vault_id,provider,account_label,local_only,local_only_acknowledged,"
                "lifecycle_state,retry_count,lag_events,lag_pages,created_at,updated_at) "
                "VALUES('bad-source',?,'fake','bad',2,0,'disabled',0,0,0,?,?)",
                (vault_id, now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE capture_checkpoints SET generation=-1 WHERE source_id=?", (source_id,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO capture_events"
                "(id,source_id,provider_event_id,provider_item_id,generation,order_key,operation,"
                "normalized_payload_json,payload_hash,status,attempts,idempotency_key,received_at) "
                "VALUES('bad-event',?,'bad-event','bad-item',-1,'1','upsert','{}',?,"
                "'staged',0,'bad-idempotency',?)",
                (source_id, "0" * 64, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO capture_items"
                "(source_id,provider_item_id,canonical_record_id,generation,last_event_id,item_state,"
                "updated_at) "
                "VALUES(?, 'bad-item', 'bad-record', 0, 'bad-event', 'invalid', ?)",
                (source_id, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO capture_runs"
                "(id,source_id,state,lease_token,lease_expires_at,started_at) "
                "VALUES('bad-run',?,'invalid','token',?,?)",
                (source_id, now, now),
            )


def test_stale_coordinator_cannot_mutate_after_recovery_and_successful_replacement(
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    database_path = tmp_path / "shared.sqlite3"
    store = CoreStore(database_path)
    store.initialize_vault()
    coordinator_a = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    source_id = _source(coordinator_a)
    _enable(coordinator_a, source_id)
    handle_a, _source_a, _attempt_a = coordinator_a.ledger.begin_run(source_id)
    event_a = _event("a-old", "item-a", "1")
    event_id_a, _already_applied, _attempts = coordinator_a.ledger.stage_event(handle_a, event_a)

    clock.value = "2026-01-01T00:01:01.000000Z"
    assert coordinator_a.ledger.recover_expired_runs() == 1
    coordinator_b = CaptureCoordinator(
        CoreStore(database_path), clock=clock, sink=IdempotentFakeSink()
    )
    coordinator_b.resume(source_id)
    coordinator_b.register_adapter(
        "fake",
        DeterministicFakeAdapter(
            [CapturePage(generation=1, events=(_event("b-new", "item-b", "2"),))]
        ),
    )
    successful = coordinator_b.run(source_id)
    assert successful.status == "completed"
    assert coordinator_b.get_source(source_id).lifecycle_state == "enabled"

    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator_a.ledger.stage_event(handle_a, _event("a-new", "item-c", "3"))
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator_a.ledger.commit_event(
            handle=handle_a,
            event=event_a,
            event_id=event_id_a,
            receipt="late-receipt",
            canonical_record_id="late-record",
        )
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator_a.ledger.commit_page_cursor(
            handle_a,
            CapturePage(generation=1, next_cursor="late-cursor", done=True),
        )
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator_a.ledger.mark_event_failure(handle_a, event_id_a, "capture_sink_failed")
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator_a.ledger.finish_run(
            handle=handle_a,
            status="failed",
            error_code="capture_sink_failed",
            pages=1,
            events=1,
            applied_events=0,
            duplicate_events=0,
            failures=1,
            attempts=1,
            backoff=BackoffPolicy(),
        )

    with coordinator_a.ledger.store.connect() as connection:
        old_run = connection.execute(
            "SELECT state FROM capture_runs WHERE id=?", (handle_a.run_id,)
        ).fetchone()
        old_event = connection.execute(
            "SELECT status FROM capture_events WHERE id=?", (event_id_a,)
        ).fetchone()
        item = connection.execute(
            "SELECT 1 FROM capture_items WHERE source_id=? AND provider_item_id='item-a'",
            (source_id,),
        ).fetchone()
        checkpoint = connection.execute(
            "SELECT last_order_key,cursor FROM capture_checkpoints WHERE source_id=?",
            (source_id,),
        ).fetchone()
    assert old_run is not None and old_run["state"] == "abandoned"
    assert old_event is not None and old_event["status"] == "staged"
    assert item is None
    assert checkpoint is not None and checkpoint["last_order_key"] == "2"
    assert coordinator_b.get_source(source_id).lifecycle_state == "enabled"


@pytest.mark.parametrize("transition", ["pause", "revoke"])
def test_pause_or_revoke_blocks_later_run_handle_writes(tmp_path: Path, transition: str) -> None:
    clock = _MutableClock()
    store = CoreStore(tmp_path / f"{transition}.sqlite3")
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    handle, _source_projection, _attempt = coordinator.ledger.begin_run(source_id)
    event = _event("held", "held-item", "1")
    event_id, _already_applied, _attempts = coordinator.ledger.stage_event(handle, event)
    getattr(coordinator, transition)(source_id)

    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator.ledger.stage_event(handle, _event("late", "late-item", "2"))
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator.ledger.commit_event(
            handle=handle,
            event=event,
            event_id=event_id,
            receipt="late-receipt",
            canonical_record_id="late-record",
        )
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator.ledger.commit_page_cursor(handle, CapturePage(generation=1, done=True))
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator.ledger.mark_event_failure(handle, event_id, "capture_sink_failed")
    with pytest.raises(CaptureError, match="capture_lease_expired"):
        coordinator.ledger.finish_run(
            handle=handle,
            status="completed",
            error_code=None,
            pages=1,
            events=1,
            applied_events=1,
            duplicate_events=0,
            failures=0,
            attempts=1,
            backoff=BackoffPolicy(),
        )

    with coordinator.ledger.store.connect() as connection:
        run = connection.execute(
            "SELECT state FROM capture_runs WHERE id=?", (handle.run_id,)
        ).fetchone()
        stored_event = connection.execute(
            "SELECT status FROM capture_events WHERE id=?", (event_id,)
        ).fetchone()
        checkpoint = connection.execute(
            "SELECT last_order_key,cursor FROM capture_checkpoints WHERE source_id=?",
            (source_id,),
        ).fetchone()
    assert coordinator.get_source(source_id).lifecycle_state == (
        "paused" if transition == "pause" else "revoked"
    )
    assert run is not None and run["state"] == "abandoned"
    assert stored_event is not None and stored_event["status"] == "staged"
    assert checkpoint is not None and checkpoint["last_order_key"] is None
    assert checkpoint["cursor"] is None


def test_lease_expiry_before_sink_does_not_stage_or_call_sink(tmp_path: Path) -> None:
    clock = _MutableClock()
    sink = IdempotentFakeSink()
    store = CoreStore(tmp_path / "before-sink.sqlite3")
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=sink)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    adapter = _GatedAdapter(
        CapturePage(generation=1, events=(_event("before", "item", "1"),)),
        lambda: setattr(clock, "value", "2026-01-01T00:01:01.000000Z"),
    )
    coordinator.register_adapter("fake", adapter)

    result = coordinator.run(source_id)

    assert result.error_code == "capture_lease_expired"
    assert sink.calls == []
    with coordinator.ledger.store.connect() as connection:
        event = connection.execute(
            "SELECT 1 FROM capture_events WHERE source_id=?", (source_id,)
        ).fetchone()
        run = connection.execute(
            "SELECT state FROM capture_runs WHERE source_id=? ORDER BY started_at DESC LIMIT 1",
            (source_id,),
        ).fetchone()
    assert event is None
    assert run is not None and run["state"] == "running"


def test_lease_expiry_during_sink_replays_same_idempotency_key(tmp_path: Path) -> None:
    clock = _MutableClock()
    sink = _ExpiringSink(clock)
    store = CoreStore(tmp_path / "during-sink.sqlite3")
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=sink)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    page = CapturePage(generation=1, events=(_event("during", "item", "1"),))
    coordinator.register_adapter("fake", DeterministicFakeAdapter([page]))

    first = coordinator.run(source_id)
    assert first.error_code == "capture_lease_expired"
    assert len(sink.calls) == 1
    coordinator.ledger.recover_expired_runs()
    coordinator.resume(source_id)
    coordinator.register_adapter("fake", DeterministicFakeAdapter([page]))
    replay = coordinator.run(source_id)

    assert replay.status == "completed"
    assert len(sink.calls) == 1
    with coordinator.ledger.store.connect() as connection:
        event = connection.execute(
            "SELECT status,idempotency_key FROM capture_events WHERE source_id=?",
            (source_id,),
        ).fetchone()
        item = connection.execute(
            "SELECT item_state FROM capture_items WHERE source_id=?", (source_id,)
        ).fetchone()
    assert event is not None and event["status"] == "applied"
    assert event["idempotency_key"] == sink.calls[0][2]
    assert item is not None and item["item_state"] == "active"


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
    _enable(coordinator, source_id)
    handle = _begin(coordinator, source_id)
    event_id, _already_applied, _attempts = coordinator.ledger.stage_event(
        handle, _event("e1", "i1", "1", payload={"a": "one"})
    )
    with pytest.raises(CaptureError, match="capture_event_payload_conflict"):
        coordinator.ledger.stage_event(handle, _event("e1", "i1", "1", payload={"a": "two"}))
    with pytest.raises(CaptureError, match="capture_event_payload_conflict"):
        coordinator.ledger.commit_event(
            handle=handle,
            event=_event("e2", "i1", "1", payload={"a": "one"}),
            event_id=event_id,
            receipt="receipt-1",
            canonical_record_id="record-1",
        )
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

    coordinator.resume(source_id)
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
    _enable(coordinator, source_id)
    handle = _begin(coordinator, source_id)
    with pytest.raises(CaptureError, match="capture_payload_rejected"):
        coordinator.ledger.stage_event(
            handle,
            _event("e1", "i1", "1", payload={"api_token": "not-persisted"}),
        )
    with coordinator.ledger.store.connect() as connection:
        dump = " ".join(
            str(row[0])
            for table in ("capture_sources", "capture_events")
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
        )
    assert "do-not-store" not in dump and "not-persisted" not in dump


@pytest.mark.parametrize(
    "payload",
    (
        {"\uff41\uff50\uff49\uff3f\uff54\uff4f\uff4b\uff45\uff4e": "unicode-key-canary"},
        {"to\u200bken": "zero-width-key-canary"},
        {"safe": "\uff22\uff45\uff41\uff52\uff45\uff52 unicode-value-canary"},
    ),
)
def test_unicode_secret_markers_never_enter_capture_state(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    coordinator = _coordinator(tmp_path)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    handle = _begin(coordinator, source_id)

    with pytest.raises(CaptureError, match="capture_payload_rejected"):
        coordinator.ledger.stage_event(
            handle,
            _event("unicode-event", "unicode-item", "1", payload=payload),
        )

    with coordinator.ledger.store.connect() as connection:
        dump = " ".join(
            str(row[0])
            for row in connection.execute(
                "SELECT normalized_payload_json FROM capture_events"
            ).fetchall()
        )
    assert "canary" not in dump


def test_capture_contract_rejects_implicit_identifier_and_integer_coercions() -> None:
    with pytest.raises(CaptureError, match="capture_page_malformed"):
        CaptureEvent(  # type: ignore[arg-type]
            provider_event_id=1,
            provider_item_id="item",
            order_key="1",
        )
    with pytest.raises(CaptureError, match="capture_page_malformed"):
        CaptureEvent(
            provider_event_id="event",
            provider_item_id="item",
            order_key="1",
            generation=True,  # type: ignore[arg-type]
        )
    with pytest.raises(CaptureError, match="capture_page_malformed"):
        CapturePage(generation=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid capture backoff policy"):
        BackoffPolicy(base_seconds=True)  # type: ignore[arg-type]


def test_capture_contract_rejects_out_of_range_integers_before_sqlite_state() -> None:
    with pytest.raises(CaptureError, match="capture_page_malformed"):
        CapturePage(generation=1 << 63)
    with pytest.raises(CaptureError, match="capture_page_malformed"):
        CaptureEvent(
            provider_event_id="event",
            provider_item_id="item",
            order_key="1",
            generation=1 << 63,
        )
    oversized_payload = CaptureEvent(
        provider_event_id="event",
        provider_item_id="item",
        order_key="1",
        payload={"count": 1 << 63},
    )
    with pytest.raises(CaptureError, match="capture_payload_rejected"):
        oversized_payload.normalized()


def test_unexpected_capture_failure_closes_run_and_degrades_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = _coordinator(tmp_path)
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    coordinator.register_adapter(
        "fake",
        DeterministicFakeAdapter([CapturePage(generation=1, events=(_event("e1", "i1", "1"),))]),
    )

    def fail_stage(*_args: Any, **_kwargs: Any) -> Any:
        raise OverflowError("synthetic SQLite integer overflow")

    monkeypatch.setattr(coordinator.ledger, "stage_event", fail_stage)
    result = coordinator.run(source_id)

    assert result.status == "failed"
    assert result.error_code == "capture_failed"
    assert coordinator.get_source(source_id).lifecycle_state == "degraded"
    with coordinator.ledger.store.connect() as connection:
        run = connection.execute(
            "SELECT state,error_code FROM capture_runs WHERE source_id=?",
            (source_id,),
        ).fetchone()
    assert run is not None and tuple(run) == ("failed", "capture_failed")


def test_sink_cannot_redirect_first_event_to_noncanonical_lineage(tmp_path: Path) -> None:
    class MisdirectedSink:
        def apply(self, event: Any, **kwargs: Any) -> CaptureApplicationReceipt:
            del event, kwargs
            return CaptureApplicationReceipt(
                receipt="synthetic-receipt",
                canonical_record_id="attacker-selected-record",
            )

    coordinator = CaptureCoordinator(_store(tmp_path), sink=MisdirectedSink())
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    coordinator.register_adapter(
        "fake",
        DeterministicFakeAdapter([CapturePage(generation=1, events=(_event("e1", "i1", "1"),))]),
    )

    result = coordinator.run(source_id)

    assert result.status == "failed"
    assert result.error_code == "capture_sink_receipt_invalid"
    with coordinator.ledger.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_items").fetchone()[0] == 0
        event = connection.execute(
            "SELECT status,error_code FROM capture_events WHERE source_id=?",
            (source_id,),
        ).fetchone()
    assert tuple(event) == ("staged", "capture_sink_receipt_invalid")


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
