"""Small clean-vault, zero-dashboard Packet F usefulness journey."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from allthecontext.capture import CaptureRetryableError
from allthecontext.capture_scheduler import SchedulerConfig
from allthecontext.core.service import CoreService
from allthecontext.experimental_local_git_workspace_connector import (
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.models import MemoryTruthStatus
from allthecontext.storage import NotFoundError

from tests.fixtures.local_git_workspace import create_sanitized_workspace
from tests.fixtures.scheduled_packet_f import (
    DELETE_RELATIVE_PATH,
    POST_UPDATE_FORBIDDEN,
    UPDATE_RELATIVE_PATH,
    UPDATED_SOURCE_BYTES,
    MutableClock,
    assert_leak_oracle,
    assert_public_truth_has_no_raw_material,
    assert_status_content_free,
    authorize_and_enable_scheduled_workspace,
    config,
    current_truth,
    deleted_truth,
    item_for_event,
    open_process_gate,
    search,
    truth_summary,
    upsert_event,
    workspace_adapter,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / ("scheduled_packet_f_acceptance.json")
)


def _contract() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
    interval: float = 0.01,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition was not met before timeout")


def _advance_clock_and_wake(
    service: CoreService,
    clock: MutableClock,
    seconds: int,
) -> None:
    clock.advance(seconds)
    service.capture_scheduler._wakeup.set()


def _wait_for_successful_capture(
    service: CoreService,
    source_id: str,
    clock: MutableClock,
    *,
    current_items: int,
    deleted_items: int = 0,
) -> None:
    # A loaded Windows xdist worker can spend several seconds scheduling the
    # non-daemon Core worker and its SQLite work. The predicate returns as soon
    # as the durable capture projection is complete, so this larger bound adds
    # no delay to successful runs while keeping lifecycle failures bounded.
    capture_timeout = 15.0
    expected_last_run_at = clock()

    def captured() -> bool:
        source = service.capture.get_source(source_id)
        return (
            source.lifecycle_state == "enabled"
            and source.next_retry_at is None
            and source.last_error_code is None
            and source.last_run_at == expected_last_run_at
            and len(current_truth(service).items) == current_items
            and len(deleted_truth(service).items) == deleted_items
            and search(service.retrieval).total == current_items
        )

    _wait_until(captured, timeout=capture_timeout)


def test_scheduled_packet_f_local_source_is_useful_without_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    open_process_gate(monkeypatch)
    config_for_vault = config(tmp_path)
    workspace = create_sanitized_workspace(tmp_path / "workspace")
    clock = MutableClock()
    incremental_interval = SchedulerConfig().incremental_interval_seconds
    dashboard_calls: list[str] = []

    def unexpected_dashboard_call(url: str) -> bool:
        dashboard_calls.append(url)
        raise AssertionError("scheduled local capture must not open the dashboard")

    monkeypatch.setattr("allthecontext.desktop_setup.open_dashboard", unexpected_dashboard_call)

    original_fetch_page = LocalGitWorkspaceCaptureProviderAdapter.fetch_page
    fetch_calls = 0

    def fail_once(
        adapter: LocalGitWorkspaceCaptureProviderAdapter,
        source: Any,
        cursor: str | None,
        page_order: int,
    ) -> Any:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            raise CaptureRetryableError()
        return original_fetch_page(adapter, source, cursor, page_order)

    monkeypatch.setattr(LocalGitWorkspaceCaptureProviderAdapter, "fetch_page", fail_once)

    with CoreService(config_for_vault, clock=clock) as service:
        authorization = authorize_and_enable_scheduled_workspace(
            service,
            config_for_vault,
            workspace,
        )
        source_id, _enabled = authorization
        assert_leak_oracle(
            authorization,
            workspace,
            config_for_vault.data_dir,
            extra_forbidden=POST_UPDATE_FORBIDDEN,
        )

        enabled_status = service.capture_scheduler.enable()
        assert enabled_status["dispatch_allowed"] is True
        assert enabled_status["running"] is True
        assert_status_content_free(
            enabled_status,
            workspace,
            config_for_vault.data_dir,
        )

        _wait_until(
            lambda: (
                service.capture.get_source(source_id).lifecycle_state == "degraded"
                and service.capture.get_source(source_id).next_retry_at is not None
            )
        )
        failed_source = service.capture.get_source(source_id)
        assert fetch_calls == 1
        assert failed_source.last_error_code == contract["retry"]["error_code"]
        assert failed_source.next_retry_at is not None
        assert current_truth(service).items == []
        assert search(service.retrieval).total == 0

        retry_at = failed_source.next_retry_at
        assert retry_at == "2026-01-01T00:00:01.000000Z"
        assert len(current_truth(service).items) == contract["retry"]["current_items_before_resume"]
        assert_status_content_free(
            service.capture_scheduler.status(),
            workspace,
            config_for_vault.data_dir,
        )

    clock.advance(contract["retry"]["delay_seconds"])

    with CoreService(config_for_vault, clock=clock) as restarted:
        restarted_status = restarted.capture_scheduler.status()
        assert restarted_status["dispatch_allowed"] is True
        assert restarted_status["running"] is False
        persisted_source = restarted.capture.get_source(source_id)
        assert persisted_source.lifecycle_state == "degraded"
        assert persisted_source.next_retry_at == retry_at
        restarted.capture_scheduler.start()
        assert restarted.capture_scheduler.status()["running"] is True
        restarted.capture_scheduler._wakeup.set()
        _wait_for_successful_capture(
            restarted,
            source_id,
            clock,
            current_items=contract["initial"]["current_items"],
        )

        resumed_source = restarted.capture.get_source(source_id)
        assert resumed_source.last_run_at == clock()
        initial = current_truth(restarted)
        initial_search = search(restarted.retrieval)
        assert truth_summary(initial.items)["item_count"] == contract["initial"]["current_items"]
        assert initial_search.total == contract["initial"]["current_items"]
        assert_status_content_free(
            restarted.capture_scheduler.status(),
            workspace,
            config_for_vault.data_dir,
        )

        adapter = workspace_adapter(restarted)
        source = restarted.capture.get_source(source_id)
        delete_event = upsert_event(adapter, source, DELETE_RELATIVE_PATH)
        update_event = upsert_event(adapter, source, UPDATE_RELATIVE_PATH)
        deleted_before = item_for_event(initial.items, source_id, delete_event)
        updated_before = item_for_event(initial.items, source_id, update_event)

        (workspace / DELETE_RELATIVE_PATH).unlink()
        (workspace / UPDATE_RELATIVE_PATH).write_text(
            UPDATED_SOURCE_BYTES,
            encoding="utf-8",
            newline="\n",
        )
        _advance_clock_and_wake(restarted, clock, incremental_interval)
        _wait_for_successful_capture(
            restarted,
            source_id,
            clock,
            current_items=contract["incremental"]["current_items"],
            deleted_items=contract["incremental"]["deleted_items"],
        )

        current = current_truth(restarted)
        deleted = deleted_truth(restarted)
        assert (
            truth_summary(current.items)["item_count"] == contract["incremental"]["current_items"]
        )
        assert (
            truth_summary(deleted.items)["item_count"] == contract["incremental"]["deleted_items"]
        )
        assert len({item.record.id for item in current.items}) == len(current.items)
        assert len({item.record.source_reference for item in current.items}) == len(current.items)
        current_search = search(restarted.retrieval)
        assert current_search.total == contract["incremental"]["current_items"]
        assert {item.id for item in current_search.items} == {
            item.record.id for item in current.items
        }
        assert restarted.retrieval.get(deleted_before.record.id) is None
        assert restarted.retrieval.get(updated_before.record.id) is not None

        withdrawn = restarted.store.get_memory_truth(
            deleted_before.record.id,
            include_deleted=True,
        )
        assert withdrawn.status is MemoryTruthStatus.DELETED
        assert withdrawn.status_reason == "record is soft-deleted"

        with pytest.raises(NotFoundError):
            restarted.store.get_memory_truth(deleted_before.record.id, include_deleted=False)
        updated = restarted.store.get_memory_truth(
            updated_before.record.id,
            include_deleted=True,
        )
        assert updated.record.id == updated_before.record.id
        assert updated.status is MemoryTruthStatus.CURRENT
        assert updated.record.version > updated_before.record.version
        assert updated.history_count > updated_before.history_count

        assert_public_truth_has_no_raw_material(
            [*current.items, *deleted.items, withdrawn, updated],
            workspace=workspace,
            source=source,
            target_event=delete_event,
        )
        assert_public_truth_has_no_raw_material(
            [*current.items, *deleted.items, withdrawn, updated],
            workspace=workspace,
            source=source,
            target_event=update_event,
        )
        assert_leak_oracle(
            {
                "current": current.model_dump(mode="json"),
                "deleted": deleted.model_dump(mode="json"),
                "search": current_search.model_dump(mode="json"),
            },
            workspace,
            config_for_vault.data_dir,
            extra_forbidden=POST_UPDATE_FORBIDDEN,
            extra_event=update_event,
        )

        stable_current = current
        stable_deleted = deleted
        stable_search = current_search

    clock.advance(incremental_interval)
    with CoreService(config_for_vault, clock=clock) as replayed:
        replayed.capture_scheduler.start()
        assert replayed.capture_scheduler.status()["running"] is True
        replayed.capture_scheduler._wakeup.set()
        _wait_for_successful_capture(
            replayed,
            source_id,
            clock,
            current_items=contract["replay"]["current_items"],
            deleted_items=contract["replay"]["deleted_items"],
        )

        replay_current = current_truth(replayed)
        replay_deleted = deleted_truth(replayed)
        replay_search = search(replayed.retrieval)
        assert replay_current.items == stable_current.items
        assert replay_deleted.items == stable_deleted.items
        assert replay_search.items == stable_search.items
        assert replay_search.total == contract["replay"]["current_items"]
        assert len({item.record.id for item in replay_current.items}) == len(replay_current.items)
        assert len({item.record.source_reference for item in replay_current.items}) == len(
            replay_current.items
        )
        assert (
            len(replay_current.items) - len({item.record.id for item in replay_current.items})
            == contract["replay"]["duplicate_current_records"]
        )
        assert replayed.retrieval.get(deleted_before.record.id) is None
        assert replayed.retrieval.get(updated_before.record.id) is not None
        assert_status_content_free(
            replayed.capture_scheduler.status(),
            workspace,
            config_for_vault.data_dir,
        )
        assert_leak_oracle(
            {
                "current": replay_current.model_dump(mode="json"),
                "deleted": replay_deleted.model_dump(mode="json"),
                "search": replay_search.model_dump(mode="json"),
            },
            workspace,
            config_for_vault.data_dir,
            extra_forbidden=POST_UPDATE_FORBIDDEN,
        )

    assert dashboard_calls == []
