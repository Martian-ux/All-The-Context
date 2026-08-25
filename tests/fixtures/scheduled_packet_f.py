"""Shared helpers for the Packet E x Packet F scheduled capture journey.

These helpers are test evidence only. They do not start the scheduler worker
thread and do not claim Packet H, ZF-007/ZF-008 product exit, or complete
Packet E product acceptance.
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

from bench.packet_h_retrieval import (
    _bootstrap,
    _negative_query_checks,
    _provenance_packaged,
    _structural_record,
)
from bench.packet_h_truth import _public_truth_has_no_raw_material, _truth_summary

SCOPE = REGISTERED_SOURCE_CODE_OWNED_SCOPES[0]
DELETE_RELATIVE_PATH = "README.md"
UPDATE_RELATIVE_PATH = "src/app.py"
UPDATED_SOURCE_BYTES = "def greeting() -> str:\n    return 'updated'\n"
POST_UPDATE_FORBIDDEN = (
    "greeting",
    "def greeting()",
    "return 'updated'",
    "'updated'",
    UPDATED_SOURCE_BYTES,
)
EXPECTED_INITIAL_FACT_COUNTS = {
    "markdown_documentation": 2,
    "python_source": 1,
    "shell_script": 1,
}
BOOTSTRAP_BUDGET = 256


class MutableClock:
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


def config(tmp_path: Path) -> CoreConfig:
    return CoreConfig.in_directory(tmp_path / "core")


def open_process_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CAPTURE_SCHEDULER_ENABLED_ENV, "1")
    monkeypatch.delenv(UPDATE_HEALTH_OPERATION_ENV, raising=False)


def path_leak_forms(path: Path) -> frozenset[str]:
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


def assert_no_root_leak(material: Any, *roots: Path) -> None:
    rendered = material if isinstance(material, str) else json.dumps(material, default=str)
    for root in roots:
        for form in path_leak_forms(root):
            assert form not in rendered


def assert_status_content_free(status: dict[str, Any], *roots: Path) -> None:
    rendered = json.dumps(status)
    assert_no_root_leak(rendered, *roots)
    assert AUTHORIZATION_FILENAME not in rendered
    assert SCHEDULER_CONFIG_FILENAME not in rendered
    for forbidden in (
        "# Sample workspace",
        "def answer()",
        "AKIAIOSFODNN7EXAMPLE",
        "FIXTURE_SECRET",
        "workspace-source-",
        DELETE_RELATIVE_PATH,
        UPDATE_RELATIVE_PATH,
        *POST_UPDATE_FORBIDDEN,
    ):
        assert forbidden not in rendered


def payload_leak_tokens(event: CaptureEvent) -> tuple[str, ...]:
    tokens: list[str] = []
    for value in event.payload.values():
        if isinstance(value, str) and value and value not in {"text", "binary", "full", "sample"}:
            tokens.append(value)
    return tuple(tokens)


def assert_leak_oracle(
    material: Any,
    *roots: Path,
    extra_forbidden: tuple[str, ...] = (),
    extra_event: CaptureEvent | None = None,
) -> None:
    rendered = material if isinstance(material, str) else json.dumps(material, default=str)
    assert_no_root_leak(rendered, *roots)
    forbidden = list(extra_forbidden)
    if extra_event is not None:
        forbidden.extend(payload_leak_tokens(extra_event))
        forbidden.append(extra_event.provider_item_id)
    for token in forbidden:
        assert token not in rendered


def assert_negative_retrieval(
    engine: RetrievalEngine,
    extra_queries: tuple[str, ...] = (),
) -> None:
    negatives = _negative_query_checks(engine)
    assert all(bool(check["passed"]) for check in negatives.values())
    for query in extra_queries:
        response = search(engine, query=query)
        assert response.total == 0
        assert response.items == []


def binding_hash(item: Any) -> str | None:
    structured = getattr(item, "structured_value", None)
    if not isinstance(structured, dict):
        record = getattr(item, "record", None)
        structured = getattr(record, "structured_value", None)
    if not isinstance(structured, dict):
        return None
    value = structured.get("binding_hash")
    return value if isinstance(value, str) and value else None


def assert_bootstrap(
    engine: RetrievalEngine,
    *,
    excluded_id: str | None = None,
) -> Any:
    items, metadata, used = _bootstrap(engine, query="workspace item")
    assert items
    assert metadata is not None
    assert used <= BOOTSTRAP_BUDGET
    assert all(_provenance_packaged(item) for item in items)
    if excluded_id is not None:
        assert all(item.id != excluded_id for item in items)
    return items


def workspace_adapter(service: CoreService) -> LocalGitWorkspaceCaptureProviderAdapter:
    adapter = service.capture.adapters[LOCAL_GIT_WORKSPACE_PROVIDER]
    assert isinstance(adapter, LocalGitWorkspaceCaptureProviderAdapter)
    return adapter


def upsert_event(
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
    source: CaptureSource,
    relative_path: str,
) -> CaptureEvent:
    page = adapter.fetch_page(source, None, 0)
    for event in page.events:
        if event.operation == "upsert" and event.payload.get("relative_path") == relative_path:
            return event
    raise AssertionError("expected workspace item was not emitted")


def item_for_event(items: list[Any], source_id: str, event: CaptureEvent) -> Any:
    expected = registered_source_reference(source_id, event.provider_item_id)
    bound = [
        item
        for item in items
        if item.record.source_reference == expected
        and any(evidence_item.source_reference == expected for evidence_item in item.evidence)
    ]
    assert len(bound) == 1
    return bound[0]


def current_truth(service: CoreService) -> Any:
    return service.store.list_memory_truth(status=MemoryTruthStatus.CURRENT, limit=500)


def deleted_truth(service: CoreService) -> Any:
    return service.store.list_memory_truth(status=MemoryTruthStatus.DELETED, limit=500)


def search(engine: RetrievalEngine, query: str = "workspace item") -> Any:
    return engine.search(SearchRequest(query=query, scopes=[SCOPE], limit=10))


def assert_structural_search(engine: RetrievalEngine, *, expected_count: int) -> Any:
    response = search(engine)
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


def authorize_and_enable_scheduled_workspace(
    service: CoreService,
    core_config: CoreConfig,
    workspace: Path,
) -> tuple[str, Any]:
    authorized = authorize_local_workspace(
        service.store,
        core_config,
        workspace,
        local_only_acknowledged=True,
    )
    source_id = str(authorized["id"])
    enabled = service.capture.enable(source_id)
    write_scheduler_enabled(core_config.data_dir, enabled=True)
    return source_id, enabled


def assert_public_truth_has_no_raw_material(
    items: list[Any],
    *,
    workspace: Path,
    source: CaptureSource,
    target_event: CaptureEvent,
) -> None:
    assert _public_truth_has_no_raw_material(
        items,
        workspace=workspace,
        source=source,
        target_event=target_event,
    )


def truth_summary(items: list[Any]) -> dict[str, Any]:
    return _truth_summary(items)


__all__ = [
    "BOOTSTRAP_BUDGET",
    "DELETE_RELATIVE_PATH",
    "EXPECTED_INITIAL_FACT_COUNTS",
    "POST_UPDATE_FORBIDDEN",
    "SCOPE",
    "UPDATED_SOURCE_BYTES",
    "UPDATE_RELATIVE_PATH",
    "MutableClock",
    "assert_bootstrap",
    "assert_leak_oracle",
    "assert_negative_retrieval",
    "assert_no_root_leak",
    "assert_public_truth_has_no_raw_material",
    "assert_status_content_free",
    "assert_structural_search",
    "authorize_and_enable_scheduled_workspace",
    "binding_hash",
    "config",
    "current_truth",
    "deleted_truth",
    "item_for_event",
    "open_process_gate",
    "path_leak_forms",
    "payload_leak_tokens",
    "search",
    "truth_summary",
    "upsert_event",
    "workspace_adapter",
]
