"""Packet E x Packet F scheduled composition evidence.

This is a focused CoreService proof that the already-merged opt-in Packet E
scheduler drives authorized Packet F local-workspace ingestion through public
Memory Truth and Retrieval V3. The two tests call
``capture_scheduler.run_cycle()``, the same method used by the background
loop, without starting that thread. The proof reuses Packet H-D
truth/retrieval helpers but is not Packet H.

It is not ZF-007/ZF-008 product exit, complete Packet E product acceptance,
complete Packet H, Phase 2, provider or client support, macOS support, or
universal continuous capture. Continuous/scheduled Packet F acceptance
remains open.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.capture_runtime import write_scheduler_enabled
from allthecontext.capture_scheduler import (
    CAPTURE_SCHEDULER_ENABLED_ENV,
    UPDATE_HEALTH_OPERATION_ENV,
    SchedulerConfig,
)
from allthecontext.core.service import CoreService
from allthecontext.experimental_local_git_workspace_connector import LOCAL_GIT_WORKSPACE_PROVIDER
from allthecontext.models import MemoryTruthStatus
from allthecontext.storage import NotFoundError

from bench.packet_h_retrieval import _safe_record_signature, _structural_record
from bench.packet_h_truth import (
    _coverage_summary,
    _truth_collections_match,
    _withdrawal_state_is_exact,
)
from tests.fixtures.local_git_workspace import create_sanitized_workspace
from tests.fixtures.scheduled_packet_f import (
    DELETE_RELATIVE_PATH,
    EXPECTED_INITIAL_FACT_COUNTS,
    POST_UPDATE_FORBIDDEN,
    UPDATE_RELATIVE_PATH,
    UPDATED_SOURCE_BYTES,
    MutableClock,
    assert_bootstrap,
    assert_leak_oracle,
    assert_negative_retrieval,
    assert_public_truth_has_no_raw_material,
    assert_status_content_free,
    assert_structural_search,
    authorize_and_enable_scheduled_workspace,
    binding_hash,
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


def test_scheduled_packet_f_drives_public_truth_and_retrieval_v3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_process_gate(monkeypatch)
    core_config = config(tmp_path)
    workspace = create_sanitized_workspace(tmp_path / "workspace")
    clock = MutableClock()
    interval = SchedulerConfig().incremental_interval_seconds

    with CoreService(core_config, clock=clock) as service:
        source_id, enabled = authorize_and_enable_scheduled_workspace(
            service, core_config, workspace
        )

        first_status = service.capture_scheduler.status()
        repeat_status = service.capture_scheduler.status()
        assert first_status == repeat_status
        assert first_status["dispatch_allowed"] is True
        assert first_status["reason_code"] == "enabled"
        assert first_status["running"] is False
        assert_status_content_free(first_status, workspace, core_config.data_dir)

        first = service.capture_scheduler.run_cycle()
        assert first.plan.enabled is True
        assert first.dispatched[0].source_id == source_id
        assert first.dispatched[0].kind == "initial_backfill"
        assert first.results[0].status == "completed"
        assert first.results[0].applied_events == 4

        adapter = workspace_adapter(service)
        source = service.capture.get_source(source_id)
        delete_event = upsert_event(adapter, source, DELETE_RELATIVE_PATH)
        update_event = upsert_event(adapter, source, UPDATE_RELATIVE_PATH)

        current_before = current_truth(service)
        coverage_before = service.store.memory_truth_coverage()
        details_before = [
            service.store.get_memory_truth(item.record.id, include_deleted=True)
            for item in current_before.items
        ]
        before_summary = truth_summary(current_before.items)
        assert enabled.lifecycle_state == "enabled"
        assert before_summary["item_count"] == 4
        assert before_summary["status_counts"] == {"current": 4}
        assert before_summary["fact_class_counts"] == EXPECTED_INITIAL_FACT_COUNTS
        assert before_summary["all_core_available"] is True
        assert before_summary["all_normal_sensitivity"] is True
        assert before_summary["all_registered_source_provenance"] is True
        assert before_summary["all_registered_capture_type"] is True
        assert before_summary["all_applied_evidence"] is True
        assert _truth_collections_match(current_before.items, details_before)
        before_coverage = _coverage_summary(coverage_before)
        assert before_coverage["record_count"] == 4
        assert before_coverage["records_by_status"]["current"] == 4
        assert before_coverage["records_by_status"]["deleted"] == 0
        assert_public_truth_has_no_raw_material(
            [*current_before.items, *details_before],
            workspace=workspace,
            source=source,
            target_event=delete_event,
        )

        search_before = assert_structural_search(service.retrieval, expected_count=4)
        assert_negative_retrieval(service.retrieval)
        assert_bootstrap(service.retrieval)

        idle = service.capture_scheduler.run_cycle()
        idle_current = current_truth(service)
        idle_search = search(service.retrieval)
        assert idle.dispatched == ()
        assert idle.results == ()
        assert _truth_collections_match(current_before.items, idle_current.items)
        assert _coverage_summary(service.store.memory_truth_coverage()) == _coverage_summary(
            coverage_before
        )
        assert _safe_record_signature(idle_search.items) == _safe_record_signature(
            search_before.items
        )

        withdrawn_before = item_for_event(details_before, source_id, delete_event)
        updated_before = item_for_event(details_before, source_id, update_event)
        clock.advance(interval)
        (workspace / DELETE_RELATIVE_PATH).unlink()
        (workspace / UPDATE_RELATIVE_PATH).write_text(
            UPDATED_SOURCE_BYTES,
            encoding="utf-8",
            newline="\n",
        )
        second = service.capture_scheduler.run_cycle()
        assert second.dispatched[0].kind == "incremental"
        assert second.results[0].status == "completed"
        assert second.results[0].applied_events == 2
        assert second.results[0].events == 2

        current_after = current_truth(service)
        deleted_after = deleted_truth(service)
        coverage_after = service.store.memory_truth_coverage()
        after_summary = truth_summary(current_after.items)
        deleted_summary = truth_summary(deleted_after.items)
        withdrawn_after = service.store.get_memory_truth(
            withdrawn_before.record.id,
            include_deleted=True,
        )
        with pytest.raises(NotFoundError):
            service.store.get_memory_truth(withdrawn_before.record.id, include_deleted=False)
        updated_after = service.store.get_memory_truth(
            updated_before.record.id,
            include_deleted=True,
        )
        current_ids = {item.record.id for item in current_after.items}
        current_refs = {item.record.source_reference for item in current_after.items}

        assert after_summary["item_count"] == 3
        assert after_summary["status_counts"] == {"current": 3}
        assert after_summary["fact_class_counts"] == {
            "markdown_documentation": 1,
            "python_source": 1,
            "shell_script": 1,
        }
        assert deleted_summary["item_count"] == 1
        assert deleted_summary["status_counts"] == {"deleted": 1}
        assert withdrawn_before.record.id not in current_ids
        assert withdrawn_after.status is MemoryTruthStatus.DELETED
        assert withdrawn_after.status_reason == "record is soft-deleted"
        assert _withdrawal_state_is_exact(withdrawn_before, withdrawn_after)
        assert deleted_after.items[0].record.id == withdrawn_before.record.id
        assert updated_after.record.id == updated_before.record.id
        assert updated_after.status is MemoryTruthStatus.CURRENT
        assert updated_after.record.id in current_ids
        assert len(current_ids) == len(current_refs) == 3
        assert updated_after.record.version > updated_before.record.version
        assert updated_after.history_count > updated_before.history_count
        assert {item.observation_id for item in updated_after.evidence} != {
            item.observation_id for item in updated_before.evidence
        }
        before_binding = binding_hash(updated_before)
        after_binding = binding_hash(updated_after)
        assert before_binding is not None
        assert after_binding is not None
        assert after_binding != before_binding
        after_coverage = _coverage_summary(coverage_after)
        assert after_coverage["record_count"] == 4
        assert after_coverage["records_by_status"]["current"] == 3
        assert after_coverage["records_by_status"]["deleted"] == 1

        search_after = assert_structural_search(service.retrieval, expected_count=3)
        assert service.retrieval.get(withdrawn_before.record.id) is None
        fetched_updated = service.retrieval.get(updated_before.record.id)
        assert fetched_updated is not None
        assert fetched_updated.id == updated_before.record.id
        assert _structural_record(fetched_updated, "python_source")
        fetched_binding = binding_hash(fetched_updated)
        assert fetched_binding == after_binding
        assert_bootstrap(service.retrieval, excluded_id=withdrawn_before.record.id)
        assert_negative_retrieval(
            service.retrieval,
            extra_queries=("greeting", "updated", "def greeting"),
        )
        updated_event_after = upsert_event(adapter, source, UPDATE_RELATIVE_PATH)
        assert_status_content_free(
            service.capture_scheduler.status(),
            workspace,
            core_config.data_dir,
        )
        public_after = [
            *current_after.items,
            *deleted_after.items,
            withdrawn_after,
            updated_after,
        ]
        leak_material = {
            "truth": current_after.model_dump(mode="json"),
            "deleted": deleted_after.model_dump(mode="json"),
            "search": search_after.model_dump(mode="json"),
        }
        assert_leak_oracle(
            leak_material,
            workspace,
            core_config.data_dir,
            extra_forbidden=POST_UPDATE_FORBIDDEN,
            extra_event=updated_event_after,
        )
        assert_public_truth_has_no_raw_material(
            public_after,
            workspace=workspace,
            source=source,
            target_event=delete_event,
        )
        assert_public_truth_has_no_raw_material(
            public_after,
            workspace=workspace,
            source=source,
            target_event=updated_event_after,
        )

        cycle_two_current = current_after
        cycle_two_deleted = deleted_after
        cycle_two_coverage = coverage_after
        cycle_two_search = search_after
        withdrawn_id = withdrawn_before.record.id
        updated_id = updated_before.record.id

    clock.advance(interval)
    with CoreService(core_config, clock=clock) as restarted:
        third_status = restarted.capture_scheduler.status()
        assert third_status["dispatch_allowed"] is True
        assert_status_content_free(third_status, workspace, core_config.data_dir)
        third = restarted.capture_scheduler.run_cycle()
        assert third.dispatched[0].kind == "incremental"
        assert third.results[0].status == "completed"
        assert third.results[0].applied_events == 0
        assert third.results[0].events == 0
        assert third.results[0].duplicate_events == 0

        replay_current = current_truth(restarted)
        replay_deleted = deleted_truth(restarted)
        replay_coverage = restarted.store.memory_truth_coverage()
        replay_search = assert_structural_search(restarted.retrieval, expected_count=3)
        assert _truth_collections_match(cycle_two_current.items, replay_current.items)
        assert _truth_collections_match(cycle_two_deleted.items, replay_deleted.items)
        assert _coverage_summary(replay_coverage) == _coverage_summary(cycle_two_coverage)
        assert _safe_record_signature(replay_search.items) == _safe_record_signature(
            cycle_two_search.items
        )
        assert restarted.retrieval.get(withdrawn_id) is None
        assert restarted.retrieval.get(updated_id) is not None
        assert_bootstrap(restarted.retrieval, excluded_id=withdrawn_id)
        assert_negative_retrieval(
            restarted.retrieval,
            extra_queries=("greeting", "updated", "def greeting"),
        )


def test_scheduler_negative_gates_create_zero_public_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_config = config(tmp_path)
    workspace = create_sanitized_workspace(tmp_path / "workspace")
    clock = MutableClock()

    with CoreService(core_config, clock=clock) as service:
        source_id, _enabled = authorize_and_enable_scheduled_workspace(
            service, core_config, workspace
        )
        empty = _coverage_summary(service.store.memory_truth_coverage())
        assert empty["record_count"] == 0
        assert current_truth(service).items == []

        monkeypatch.delenv(CAPTURE_SCHEDULER_ENABLED_ENV, raising=False)
        monkeypatch.delenv(UPDATE_HEALTH_OPERATION_ENV, raising=False)
        absent_env = service.capture_scheduler.run_cycle()
        absent_status = service.capture_scheduler.status()
        assert absent_status["reason_code"] == "process_gate_closed"
        assert absent_status["dispatch_allowed"] is False
        assert absent_env.plan.enabled is False
        assert absent_env.dispatched == ()
        assert current_truth(service).items == []
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        assert_status_content_free(absent_status, workspace, core_config.data_dir)

        open_process_gate(monkeypatch)
        write_scheduler_enabled(core_config.data_dir, enabled=False)
        disabled = service.capture_scheduler.run_cycle()
        disabled_status = service.capture_scheduler.status()
        assert disabled_status["reason_code"] == "disabled"
        assert disabled_status["dispatch_allowed"] is False
        assert disabled.plan.enabled is False
        assert disabled.dispatched == ()
        assert current_truth(service).items == []
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        assert_status_content_free(disabled_status, workspace, core_config.data_dir)

        write_scheduler_enabled(core_config.data_dir, enabled=True)
        monkeypatch.setenv(UPDATE_HEALTH_OPERATION_ENV, "1")
        forced = service.capture_scheduler.run_cycle()
        forced_status = service.capture_scheduler.status()
        assert forced_status["reason_code"] == "forced_off"
        assert forced_status["update_health_forced_off"] is True
        assert forced_status["dispatch_allowed"] is False
        assert forced.plan.enabled is False
        assert forced.dispatched == ()
        assert current_truth(service).items == []
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        assert_status_content_free(forced_status, workspace, core_config.data_dir)

        monkeypatch.setenv(UPDATE_HEALTH_OPERATION_ENV, "")
        empty_health = service.capture_scheduler.run_cycle()
        empty_status = service.capture_scheduler.status()
        assert empty_status["reason_code"] == "forced_off"
        assert empty_status["update_health_forced_off"] is True
        assert empty_status["dispatch_allowed"] is False
        assert empty_health.plan.enabled is False
        assert empty_health.dispatched == ()
        assert current_truth(service).items == []
        assert search(service.retrieval).total == 0
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        assert_status_content_free(empty_status, workspace, core_config.data_dir)
        assert source_id
        assert LOCAL_GIT_WORKSPACE_PROVIDER not in json.dumps(empty_status)
