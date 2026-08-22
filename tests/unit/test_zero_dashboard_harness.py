"""Focused Wave 2 Packet D disposable zero-dashboard journey tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from allthecontext import experimental_zero_dashboard_harness as harness
from allthecontext.capture import (
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureEvent,
    CapturePage,
    DeterministicFakeAdapter,
)
from allthecontext.client_runtime import (
    ClientLifecycleEnvelope,
    ContextRequestPayload,
    OrderingViolation,
)
from allthecontext.experimental_zero_dashboard_harness import (
    ZeroDashboardFixture,
    run_zero_dashboard_journey,
)
from allthecontext.storage import CoreStore


def test_zero_dashboard_wave2_journey_passes_every_non_compensable_gate(
    tmp_path: Path,
) -> None:
    fixture = ZeroDashboardFixture.from_json(
        Path(__file__).resolve().parents[1] / "fixtures" / "zero_dashboard_wave2.json"
    )

    receipt = run_zero_dashboard_journey(tmp_path / "zero-dashboard.sqlite3", fixture=fixture)

    assert receipt.scorecard.passed is True, receipt.scorecard.as_dict()
    assert receipt.scorecard.as_dict()["passed"] is True
    assert "Atlas uses deterministic local retrieval." in receipt.first_context
    assert "Atlas private staging uses a bounded fixture." in receipt.first_context
    assert "Expired Atlas working-state fixture." in receipt.first_context
    assert "Atlas uses bounded local retrieval." in receipt.corrected_context
    assert "Atlas uses deterministic local retrieval." not in receipt.corrected_context
    assert "Atlas uses deterministic local retrieval." in receipt.viewer_before_context
    assert "Atlas uses bounded local retrieval." not in receipt.viewer_context
    assert "Terminal purge fixture for Atlas." in receipt.pre_purge_context
    assert "Neptune uses a separate source." not in receipt.final_context
    assert "Expired Atlas working-state fixture." not in receipt.final_context
    assert "Temporary deletion fixture for Atlas." not in receipt.final_context
    assert "Terminal purge fixture for Atlas." not in receipt.final_context
    assert "Imported fixture text is inert evidence data." not in (
        *receipt.first_context,
        *receipt.corrected_context,
        *receipt.viewer_before_context,
        *receipt.pre_purge_context,
        *receipt.final_context,
        *receipt.viewer_context,
    )
    assert "Atlas private staging uses a bounded fixture." not in receipt.viewer_context
    assert receipt.capture_event_count == 6
    assert receipt.observation_count >= 7
    assert receipt.restart_context_latency_ms <= receipt.scorecard.restart_context_latency_bound_ms


def test_default_zero_dashboard_fixture_is_deterministic_and_sanitized() -> None:
    from allthecontext.experimental_zero_dashboard_harness import (
        default_zero_dashboard_fixture,
    )

    fixture = default_zero_dashboard_fixture()
    json_fixture = ZeroDashboardFixture.from_json(
        Path(__file__).resolve().parents[1] / "fixtures" / "zero_dashboard_wave2.json"
    )

    assert fixture == json_fixture
    assert [event.provider_event_id for page in fixture.pages for event in page.events] == [
        "capture-project",
        "capture-private",
        "capture-delete-target",
        "capture-other-project",
        "capture-expired",
        "capture-delete",
    ]
    assert all(
        "token:" not in str(event.payload).casefold()
        for page in fixture.pages
        for event in page.events
    )


def test_scorecard_rejects_poisoned_first_context(monkeypatch, tmp_path: Path) -> None:
    original = harness._compile_before_generation
    calls = 0

    def poisoned(*args: object, **kwargs: object) -> tuple[tuple[object, ...], float]:
        nonlocal calls
        items, latency = original(*args, **kwargs)  # type: ignore[arg-type]
        calls += 1
        if calls == 1:
            items = (
                *items,
                SimpleNamespace(id="poison-neptune", content="Neptune uses a separate source."),
            )
        return items, latency

    monkeypatch.setattr(harness, "_compile_before_generation", poisoned)
    receipt = run_zero_dashboard_journey(tmp_path / "poison-first.sqlite3")

    assert receipt.scorecard.passed is False
    assert receipt.scorecard.context_correctness is False
    assert "Neptune uses a separate source." in receipt.first_context


def test_scorecard_rejects_poisoned_final_import(monkeypatch, tmp_path: Path) -> None:
    original = harness._compile_before_generation
    calls = 0

    def poisoned(*args: object, **kwargs: object) -> tuple[tuple[object, ...], float]:
        nonlocal calls
        items, latency = original(*args, **kwargs)  # type: ignore[arg-type]
        calls += 1
        if calls == 4:
            items = (
                *items,
                SimpleNamespace(
                    id="poison-import", content="Imported fixture text is inert evidence data."
                ),
            )
        return items, latency

    monkeypatch.setattr(harness, "_compile_before_generation", poisoned)
    receipt = run_zero_dashboard_journey(tmp_path / "poison-final.sqlite3")

    assert receipt.scorecard.passed is False
    assert receipt.scorecard.context_correctness is False
    assert "Imported fixture text is inert evidence data." in receipt.final_context


def test_altered_lifecycle_request_parameters_are_not_ignored(monkeypatch, tmp_path: Path) -> None:
    original = harness.DeterministicFakeClientRuntimeHost.request_pre_generation_context

    def altered_request(self: object, **kwargs: object) -> object:
        result = original(self, **kwargs)  # type: ignore[arg-type]
        if not isinstance(result, ClientLifecycleEnvelope):
            return result
        if not isinstance(result.payload, ContextRequestPayload):
            return result
        return replace(
            result,
            project_id="neptune",
            payload=replace(
                result.payload,
                requested_scopes=("project:neptune",),
                budget_chars=256,
            ),
        )

    monkeypatch.setattr(
        harness.DeterministicFakeClientRuntimeHost,
        "request_pre_generation_context",
        altered_request,
    )
    with pytest.raises(OrderingViolation):
        run_zero_dashboard_journey(tmp_path / "altered-request.sqlite3")


def test_idempotent_sink_commit_failure_window_has_no_duplicate_core_state(
    tmp_path: Path,
) -> None:
    store = CoreStore(tmp_path / "commit-window.sqlite3")
    try:
        store.initialize_vault()
        principal, _token = store.create_client(harness._client_request())
        core_source = store.add_source(
            b"synthetic commit-window source",
            source_service="synthetic-capture",
            source_type="fixture",
        )
        sink = harness._FormationCaptureSink(store, principal, core_source.id)
        sink.delegate.fail_once_after_apply = True
        coordinator = CaptureCoordinator(
            store,
            sink=sink,
            clock=lambda: harness._iso(harness.ZERO_DASHBOARD_TIME),
        )
        source = coordinator.create_source(
            provider="fake",
            account_label="synthetic-commit-window",
            requested_scopes=("project:atlas",),
            local_only_acknowledged=True,
        )
        coordinator.enable(source.id)
        page = CapturePage(
            generation=1,
            page_order=0,
            done=True,
            events=(
                CaptureEvent(
                    provider_event_id="commit-window-event",
                    provider_item_id="commit-window-item",
                    order_key="1",
                    generation=1,
                    payload={
                        "kind": "working_state",
                        "content": "Commit failure window fixture for Atlas.",
                        "entity_key": "atlas",
                        "attribute_key": "commit_window",
                        "scopes": ["project:atlas"],
                        "source_reference": "commit-window",
                        "explicit_user_statement": True,
                        "project_ref": "project:atlas",
                    },
                ),
            ),
        )
        manifest = CaptureCapabilityManifest(
            provider="fake",
            network_access="denied",
            data_egress=(),
        )
        adapter = DeterministicFakeAdapter((page,), capability_manifest=manifest)
        coordinator.register_adapter("fake", adapter)

        first = coordinator.run(source.id)
        before_retry = harness._core_counts(store)
        coordinator.resume(source.id)
        second = coordinator.run(source.id)
        after_retry = harness._core_counts(store)

        assert first.error_code == "capture_sink_failed"
        assert second.status == "completed"
        assert before_retry == after_retry
        assert after_retry["observation_count"] == 1
        assert after_retry["observation_count"] == after_retry["distinct_observation_key_count"]
        assert after_retry["current_record_count"] == after_retry["distinct_record_key_count"]
        assert len(sink.delegate.calls) == 1
    finally:
        store.close()
