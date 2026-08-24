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
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from allthecontext.capture import CaptureEvent, CaptureSource
from allthecontext.capture_runtime import (
    AUTHORIZATION_FILENAME,
    SCHEDULER_CONFIG_FILENAME,
    authorize_local_workspace,
    write_scheduler_enabled,
)
from allthecontext.capture_scheduler import (
    CAPTURE_SCHEDULER_ENABLED_ENV,
    UPDATE_HEALTH_OPERATION_ENV,
    SchedulerConfig,
)
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.memory_policy import (
    REGISTERED_SOURCE_CODE_OWNED_SCOPES,
    REGISTERED_SOURCE_FACT_SENTENCES,
    registered_source_reference,
)
from allthecontext.models import MemoryTruthStatus, SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.storage import NotFoundError

from bench.packet_h_retrieval import (
    _bootstrap,
    _negative_query_checks,
    _provenance_packaged,
    _safe_record_signature,
    _structural_record,
)
from bench.packet_h_truth import (
    _coverage_summary,
    _public_truth_has_no_raw_material,
    _truth_collections_match,
    _truth_summary,
    _withdrawal_state_is_exact,
)
from tests.fixtures.local_git_workspace import create_sanitized_workspace

_SCOPE = REGISTERED_SOURCE_CODE_OWNED_SCOPES[0]
_DELETE_RELATIVE_PATH = "README.md"
_UPDATE_RELATIVE_PATH = "src/app.py"
_UPDATED_SOURCE_BYTES = "def greeting() -> str:\n    return 'updated'\n"
_POST_UPDATE_FORBIDDEN = (
    "greeting",
    "def greeting()",
    "return 'updated'",
    "'updated'",
    _UPDATED_SOURCE_BYTES,
)
_EXPECTED_INITIAL_FACT_COUNTS = {
    "markdown_documentation": 2,
    "python_source": 1,
    "shell_script": 1,
}
_BOOTSTRAP_BUDGET = 256


class _MutableClock:
    def __init__(self, value: str = "2026-01-01T00:00:00.000000Z") -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value

    def advance(self, seconds: int) -> None:
        current = datetime.fromisoformat(self.value.replace("Z", "+00:00"))
        self.value = (
            (current + timedelta(seconds=seconds))
            .astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )


def _config(tmp_path: Path) -> CoreConfig:
    return CoreConfig.in_directory(tmp_path / "core")


def _open_process_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CAPTURE_SCHEDULER_ENABLED_ENV, "1")
    monkeypatch.delenv(UPDATE_HEALTH_OPERATION_ENV, raising=False)


def _path_leak_forms(path: Path) -> frozenset[str]:
    resolved = path.resolve()
    forms = {
        str(path),
        str(resolved),
        path.as_posix(),
        resolved.as_posix(),
        os.fspath(path),
        os.fspath(resolved),
    }
    escaped = {json.dumps(form)[1:-1] for form in forms}
    return frozenset(form for form in forms | escaped if form)


def _assert_no_root_leak(material: Any, *roots: Path) -> None:
    rendered = material if isinstance(material, str) else json.dumps(material, default=str)
    for root in roots:
        for form in _path_leak_forms(root):
            assert form not in rendered


def _assert_status_content_free(status: dict[str, Any], *roots: Path) -> None:
    rendered = json.dumps(status)
    _assert_no_root_leak(rendered, *roots)
    assert AUTHORIZATION_FILENAME not in rendered
    assert SCHEDULER_CONFIG_FILENAME not in rendered
    for forbidden in (
        "# Sample workspace",
        "def answer()",
        "AKIAIOSFODNN7EXAMPLE",
        "FIXTURE_SECRET",
        "workspace-source-",
        _DELETE_RELATIVE_PATH,
        _UPDATE_RELATIVE_PATH,
        *_POST_UPDATE_FORBIDDEN,
    ):
        assert forbidden not in rendered


def _payload_leak_tokens(event: CaptureEvent) -> tuple[str, ...]:
    tokens: list[str] = []
    for value in event.payload.values():
        if isinstance(value, str) and value and value not in {"text", "binary", "full", "sample"}:
            tokens.append(value)
    return tuple(tokens)


def _assert_leak_oracle(
    material: Any,
    *roots: Path,
    extra_forbidden: tuple[str, ...] = (),
    extra_event: CaptureEvent | None = None,
) -> None:
    rendered = material if isinstance(material, str) else json.dumps(material, default=str)
    _assert_no_root_leak(rendered, *roots)
    forbidden = list(extra_forbidden)
    if extra_event is not None:
        forbidden.extend(_payload_leak_tokens(extra_event))
        forbidden.append(extra_event.provider_item_id)
    for token in forbidden:
        assert token not in rendered


def _assert_negative_retrieval(
    engine: RetrievalEngine,
    extra_queries: tuple[str, ...] = (),
) -> None:
    negatives = _negative_query_checks(engine)
    assert all(bool(check["passed"]) for check in negatives.values())
    for query in extra_queries:
        response = _search(engine, query=query)
        assert response.total == 0
        assert response.items == []


def _binding_hash(item: Any) -> str | None:
    structured = getattr(item, "structured_value", None)
    if not isinstance(structured, dict):
        record = getattr(item, "record", None)
        structured = getattr(record, "structured_value", None)
    if not isinstance(structured, dict):
        return None
    value = structured.get("binding_hash")
    return value if isinstance(value, str) and value else None


def _assert_bootstrap(
    engine: RetrievalEngine,
    *,
    excluded_id: str | None = None,
) -> Any:
    items, metadata, used = _bootstrap(engine, query="workspace item")
    assert items
    assert metadata is not None
    assert used <= _BOOTSTRAP_BUDGET
    assert all(_provenance_packaged(item) for item in items)
    if excluded_id is not None:
        assert all(item.id != excluded_id for item in items)
    return items


def _workspace_adapter(service: CoreService) -> LocalGitWorkspaceCaptureProviderAdapter:
    adapter = service.capture.adapters[LOCAL_GIT_WORKSPACE_PROVIDER]
    assert isinstance(adapter, LocalGitWorkspaceCaptureProviderAdapter)
    return adapter


def _upsert_event(
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
    source: CaptureSource,
    relative_path: str,
) -> CaptureEvent:
    page = adapter.fetch_page(source, None, 0)
    for event in page.events:
        if event.operation == "upsert" and event.payload.get("relative_path") == relative_path:
            return event
    raise AssertionError("expected workspace item was not emitted")


def _item_for_event(items: list[Any], source_id: str, event: CaptureEvent) -> Any:
    expected = registered_source_reference(source_id, event.provider_item_id)
    bound = [
        item
        for item in items
        if item.record.source_reference == expected
        and any(evidence_item.source_reference == expected for evidence_item in item.evidence)
    ]
    assert len(bound) == 1
    return bound[0]


def _current_truth(service: CoreService) -> Any:
    return service.store.list_memory_truth(status=MemoryTruthStatus.CURRENT, limit=500)


def _deleted_truth(service: CoreService) -> Any:
    return service.store.list_memory_truth(status=MemoryTruthStatus.DELETED, limit=500)


def _search(engine: RetrievalEngine, query: str = "workspace item") -> Any:
    return engine.search(SearchRequest(query=query, scopes=[_SCOPE], limit=10))


def _assert_structural_search(engine: RetrievalEngine, *, expected_count: int) -> Any:
    response = _search(engine)
    expected_sentences = set(REGISTERED_SOURCE_FACT_SENTENCES.values())
    structural = [
        item
        for item in response.items
        if item.content in expected_sentences and _provenance_packaged(item)
    ]
    assert response.total == expected_count
    assert len(response.items) == len(structural) == expected_count
    for item in structural:
        fact_class = item.structured_value["fact_class"]
        assert isinstance(fact_class, str)
        assert _structural_record(item, fact_class)
        fetched = engine.get(item.id)
        assert fetched is not None
        assert fetched.model_dump(mode="json") == item.model_dump(mode="json")
    return response


def test_scheduled_packet_f_drives_public_truth_and_retrieval_v3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    workspace = create_sanitized_workspace(tmp_path / "workspace")
    clock = _MutableClock()
    interval = SchedulerConfig().incremental_interval_seconds

    with CoreService(config, clock=clock) as service:
        authorized = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        source_id = str(authorized["id"])
        enabled = service.capture.enable(source_id)
        write_scheduler_enabled(config.data_dir, enabled=True)

        first_status = service.capture_scheduler.status()
        repeat_status = service.capture_scheduler.status()
        assert first_status == repeat_status
        assert first_status["dispatch_allowed"] is True
        assert first_status["reason_code"] == "enabled"
        assert first_status["running"] is False
        _assert_status_content_free(first_status, workspace, config.data_dir)

        first = service.capture_scheduler.run_cycle()
        assert first.plan.enabled is True
        assert first.dispatched[0].source_id == source_id
        assert first.dispatched[0].kind == "initial_backfill"
        assert first.results[0].status == "completed"
        assert first.results[0].applied_events == 4

        adapter = _workspace_adapter(service)
        source = service.capture.get_source(source_id)
        delete_event = _upsert_event(adapter, source, _DELETE_RELATIVE_PATH)
        update_event = _upsert_event(adapter, source, _UPDATE_RELATIVE_PATH)

        current_before = _current_truth(service)
        coverage_before = service.store.memory_truth_coverage()
        details_before = [
            service.store.get_memory_truth(item.record.id, include_deleted=True)
            for item in current_before.items
        ]
        before_summary = _truth_summary(current_before.items)
        assert enabled.lifecycle_state == "enabled"
        assert before_summary["item_count"] == 4
        assert before_summary["status_counts"] == {"current": 4}
        assert before_summary["fact_class_counts"] == _EXPECTED_INITIAL_FACT_COUNTS
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
        assert _public_truth_has_no_raw_material(
            [*current_before.items, *details_before],
            workspace=workspace,
            source=source,
            target_event=delete_event,
        )

        search_before = _assert_structural_search(service.retrieval, expected_count=4)
        _assert_negative_retrieval(service.retrieval)
        _assert_bootstrap(service.retrieval)

        idle = service.capture_scheduler.run_cycle()
        idle_current = _current_truth(service)
        idle_search = _search(service.retrieval)
        assert idle.dispatched == ()
        assert idle.results == ()
        assert _truth_collections_match(current_before.items, idle_current.items)
        assert _coverage_summary(service.store.memory_truth_coverage()) == _coverage_summary(
            coverage_before
        )
        assert _safe_record_signature(idle_search.items) == _safe_record_signature(
            search_before.items
        )

        withdrawn_before = _item_for_event(details_before, source_id, delete_event)
        updated_before = _item_for_event(details_before, source_id, update_event)
        clock.advance(interval)
        (workspace / _DELETE_RELATIVE_PATH).unlink()
        (workspace / _UPDATE_RELATIVE_PATH).write_text(
            _UPDATED_SOURCE_BYTES,
            encoding="utf-8",
            newline="\n",
        )
        second = service.capture_scheduler.run_cycle()
        assert second.dispatched[0].kind == "incremental"
        assert second.results[0].status == "completed"
        assert second.results[0].applied_events == 2
        assert second.results[0].events == 2

        current_after = _current_truth(service)
        deleted_after = _deleted_truth(service)
        coverage_after = service.store.memory_truth_coverage()
        after_summary = _truth_summary(current_after.items)
        deleted_summary = _truth_summary(deleted_after.items)
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
        before_binding = _binding_hash(updated_before)
        after_binding = _binding_hash(updated_after)
        assert before_binding is not None
        assert after_binding is not None
        assert after_binding != before_binding
        after_coverage = _coverage_summary(coverage_after)
        assert after_coverage["record_count"] == 4
        assert after_coverage["records_by_status"]["current"] == 3
        assert after_coverage["records_by_status"]["deleted"] == 1

        search_after = _assert_structural_search(service.retrieval, expected_count=3)
        assert service.retrieval.get(withdrawn_before.record.id) is None
        fetched_updated = service.retrieval.get(updated_before.record.id)
        assert fetched_updated is not None
        assert fetched_updated.id == updated_before.record.id
        assert _structural_record(fetched_updated, "python_source")
        fetched_binding = _binding_hash(fetched_updated)
        assert fetched_binding == after_binding
        _assert_bootstrap(service.retrieval, excluded_id=withdrawn_before.record.id)
        _assert_negative_retrieval(
            service.retrieval,
            extra_queries=("greeting", "updated", "def greeting"),
        )
        updated_event_after = _upsert_event(adapter, source, _UPDATE_RELATIVE_PATH)
        _assert_status_content_free(
            service.capture_scheduler.status(),
            workspace,
            config.data_dir,
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
        _assert_leak_oracle(
            leak_material,
            workspace,
            config.data_dir,
            extra_forbidden=_POST_UPDATE_FORBIDDEN,
            extra_event=updated_event_after,
        )
        assert _public_truth_has_no_raw_material(
            public_after,
            workspace=workspace,
            source=source,
            target_event=delete_event,
        )
        assert _public_truth_has_no_raw_material(
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
    with CoreService(config, clock=clock) as restarted:
        third_status = restarted.capture_scheduler.status()
        assert third_status["dispatch_allowed"] is True
        _assert_status_content_free(third_status, workspace, config.data_dir)
        third = restarted.capture_scheduler.run_cycle()
        assert third.dispatched[0].kind == "incremental"
        assert third.results[0].status == "completed"
        assert third.results[0].applied_events == 0
        assert third.results[0].events == 0
        assert third.results[0].duplicate_events == 0

        replay_current = _current_truth(restarted)
        replay_deleted = _deleted_truth(restarted)
        replay_coverage = restarted.store.memory_truth_coverage()
        replay_search = _assert_structural_search(restarted.retrieval, expected_count=3)
        assert _truth_collections_match(cycle_two_current.items, replay_current.items)
        assert _truth_collections_match(cycle_two_deleted.items, replay_deleted.items)
        assert _coverage_summary(replay_coverage) == _coverage_summary(cycle_two_coverage)
        assert _safe_record_signature(replay_search.items) == _safe_record_signature(
            cycle_two_search.items
        )
        assert restarted.retrieval.get(withdrawn_id) is None
        assert restarted.retrieval.get(updated_id) is not None
        _assert_bootstrap(restarted.retrieval, excluded_id=withdrawn_id)
        _assert_negative_retrieval(
            restarted.retrieval,
            extra_queries=("greeting", "updated", "def greeting"),
        )


def test_scheduler_negative_gates_create_zero_public_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    workspace = create_sanitized_workspace(tmp_path / "workspace")
    clock = _MutableClock()

    with CoreService(config, clock=clock) as service:
        authorized = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        source_id = str(authorized["id"])
        service.capture.enable(source_id)
        empty = _coverage_summary(service.store.memory_truth_coverage())
        assert empty["record_count"] == 0
        assert _current_truth(service).items == []

        write_scheduler_enabled(config.data_dir, enabled=True)
        monkeypatch.delenv(CAPTURE_SCHEDULER_ENABLED_ENV, raising=False)
        monkeypatch.delenv(UPDATE_HEALTH_OPERATION_ENV, raising=False)
        absent_env = service.capture_scheduler.run_cycle()
        absent_status = service.capture_scheduler.status()
        assert absent_status["reason_code"] == "process_gate_closed"
        assert absent_status["dispatch_allowed"] is False
        assert absent_env.plan.enabled is False
        assert absent_env.dispatched == ()
        assert _current_truth(service).items == []
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        _assert_status_content_free(absent_status, workspace, config.data_dir)

        _open_process_gate(monkeypatch)
        write_scheduler_enabled(config.data_dir, enabled=False)
        disabled = service.capture_scheduler.run_cycle()
        disabled_status = service.capture_scheduler.status()
        assert disabled_status["reason_code"] == "disabled"
        assert disabled_status["dispatch_allowed"] is False
        assert disabled.plan.enabled is False
        assert disabled.dispatched == ()
        assert _current_truth(service).items == []
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        _assert_status_content_free(disabled_status, workspace, config.data_dir)

        write_scheduler_enabled(config.data_dir, enabled=True)
        monkeypatch.setenv(UPDATE_HEALTH_OPERATION_ENV, "1")
        forced = service.capture_scheduler.run_cycle()
        forced_status = service.capture_scheduler.status()
        assert forced_status["reason_code"] == "forced_off"
        assert forced_status["update_health_forced_off"] is True
        assert forced_status["dispatch_allowed"] is False
        assert forced.plan.enabled is False
        assert forced.dispatched == ()
        assert _current_truth(service).items == []
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        _assert_status_content_free(forced_status, workspace, config.data_dir)

        monkeypatch.setenv(UPDATE_HEALTH_OPERATION_ENV, "")
        empty_health = service.capture_scheduler.run_cycle()
        empty_status = service.capture_scheduler.status()
        assert empty_status["reason_code"] == "forced_off"
        assert empty_status["update_health_forced_off"] is True
        assert empty_status["dispatch_allowed"] is False
        assert empty_health.plan.enabled is False
        assert empty_health.dispatched == ()
        assert _current_truth(service).items == []
        assert _search(service.retrieval).total == 0
        assert _coverage_summary(service.store.memory_truth_coverage()) == empty
        _assert_status_content_free(empty_status, workspace, config.data_dir)
        assert source_id
        assert LOCAL_GIT_WORKSPACE_PROVIDER not in json.dumps(empty_status)
