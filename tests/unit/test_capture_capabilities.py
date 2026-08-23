"""Focused v0 capability reconciliation tests with synthetic provider state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from allthecontext.capture import (
    BackoffPolicy,
    CaptureCapabilityConformance,
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureError,
    CaptureEvent,
    CapturePage,
    CaptureRateLimitPolicy,
    CaptureRetryableError,
    CaptureRetryPolicy,
    IdempotentFakeSink,
)
from allthecontext.storage import CoreStore


def _store(tmp_path: Path, name: str = "core.sqlite3") -> CoreStore:
    store = CoreStore(tmp_path / name)
    store.initialize_vault()
    return store


def _event(event_id: str, item_id: str, order: str, *, operation: str = "upsert") -> CaptureEvent:
    return CaptureEvent(
        provider_event_id=event_id,
        provider_item_id=item_id,
        order_key=order,
        operation=operation,  # type: ignore[arg-type]
        generation=1,
    )


def _source(coordinator: CaptureCoordinator, provider: str = "fake") -> str:
    return coordinator.create_source(
        provider=provider,
        account_label="synthetic-capability-account",
        local_only_acknowledged=True,
    ).id


class _CapabilityAdapter:
    def __init__(
        self,
        manifest: CaptureCapabilityManifest,
        pages: tuple[CapturePage, ...] = (),
        *,
        retry_once: bool = False,
    ) -> None:
        self.capability_manifest = manifest
        self.pages = pages
        self.retry_once = retry_once
        self.calls: list[tuple[str | None, int]] = []

    def fetch_page(
        self,
        source: Any,
        cursor: str | None,
        page_order: int,
    ) -> CapturePage:
        del source
        self.calls.append((cursor, page_order))
        if self.retry_once:
            self.retry_once = False
            raise CaptureRetryableError(retry_after_seconds=7)
        if not self.pages:
            return CapturePage(generation=0)
        return self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]


class _LegacyFetchOnlyAdapter:
    def __init__(self, page: CapturePage) -> None:
        self.page = page
        self.calls = 0

    def fetch_page(
        self,
        source: Any,
        cursor: str | None,
        page_order: int,
    ) -> CapturePage:
        del source, cursor, page_order
        self.calls += 1
        return self.page


class _MutableClock:
    def __init__(self, value: str = "2026-01-01T00:00:00.000000Z") -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


def test_manifest_is_versioned_immutable_and_content_free() -> None:
    manifest = CaptureCapabilityManifest(
        provider="synthetic-provider",
        credential_ref="keychain:synthetic-ref-1",
        retry_policy=CaptureRetryPolicy(
            retryable_failures=True,
            max_attempts=4,
            backoff=BackoffPolicy(base_seconds=2, max_seconds=30),
            rate_limit=CaptureRateLimitPolicy(mode="retry_after", max_delay_seconds=60),
        ),
        source_deletion="coordinated",
        purge_coordination="coordinated",
        network_access=True,
        data_egress=("synthetic-provider-api",),
    )

    assert manifest.version == "v0"
    assert manifest.network_access == "allowed"
    assert manifest.conformance().valid is True
    assert manifest.model_dump()["credential_ref"] == "keychain:synthetic-ref-1"
    assert manifest.model_dump()["retry_policy"]["rate_limit"] == {
        "mode": "retry_after",
        "max_delay_seconds": 60,
    }
    with pytest.raises(AttributeError):
        manifest.provider = "changed"  # type: ignore[misc]
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(provider="synthetic-provider", credential_ref="Bearer fake-value")


def test_manifest_conformance_represents_partial_and_unavailable_truth() -> None:
    partial = CaptureCapabilityManifest(
        provider="synthetic-provider",
        availability="partial",
        coverage="partial",
        coverage_reason="scope_not_supported",
        freshness="stale",
        health="degraded",
        health_diagnostics=("coverage_limited",),
    )
    unavailable = CaptureCapabilityManifest.unavailable(
        "synthetic-provider",
        reason="provider_temporarily_unavailable",
    )

    assert partial.conformance().warnings == ("partial_coverage", "freshness_not_current")
    assert partial.coverage_state == "partial"
    assert partial.freshness_state == "stale"
    assert unavailable.availability == "unavailable"
    assert unavailable.conformance().valid is True
    assert unavailable.health_state == "unavailable"


def test_unregistered_adapter_manifest_is_unavailable_with_unknown_posture(
    tmp_path: Path,
) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_id = _source(coordinator)

    manifest = coordinator.capability_manifest(source_id)

    assert manifest.availability == "unavailable"
    assert manifest.legacy_compatibility is False
    assert manifest.connection == "unknown"
    assert manifest.network_access == "unknown"
    assert manifest.data_egress is None
    serialized = manifest.model_dump()
    assert serialized["connection"] == "unknown"
    assert serialized["network_access"] == "unknown"
    assert serialized["data_egress"] is None
    assert {"network_posture_unknown", "data_egress_unknown"}.issubset(
        set(manifest.conformance().warnings)
    )
    with coordinator.ledger.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 0


def test_manifest_rejects_contradictory_capability_claims() -> None:
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(
            provider="synthetic-provider",
            incremental=True,
            cursor_support=False,
        )
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(
            provider="synthetic-provider",
            availability="complete",
            coverage="partial",
            coverage_reason="missing_scope",
        )
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(provider="synthetic-provider", network_access=[])  # type: ignore[arg-type]
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(provider="synthetic-provider", data_egress=[])  # type: ignore[arg-type]
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(provider="synthetic-provider", connection=[])  # type: ignore[arg-type]
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(provider="synthetic-provider", network_access="unknown")
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(
            provider="synthetic-provider",
            network_access="unknown",
            data_egress=("synthetic-provider-api",),
        )
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(
            provider="synthetic-provider", network_access="denied", data_egress=None
        )
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(
            provider="synthetic-provider", network_access="allowed", data_egress=None
        )
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityManifest(
            provider="synthetic-provider",
            availability="complete",
            network_access="unknown",
            data_egress=None,
        )
    explicit_unknown = CaptureCapabilityManifest(
        provider="synthetic-provider",
        availability="partial",
        coverage="partial",
        coverage_reason="network_posture_unknown_fixture",
        network_access="unknown",
        data_egress=None,
        health="degraded",
    )
    assert explicit_unknown.conformance().valid is True
    assert explicit_unknown.conformance().warnings[-2:] == (
        "network_posture_unknown",
        "data_egress_unknown",
    )
    allowed_without_egress = CaptureCapabilityManifest(
        provider="synthetic-provider", network_access="allowed", data_egress=()
    )
    assert allowed_without_egress.conformance().valid is True
    with pytest.raises(CaptureError, match="capture_capability_invalid"):
        CaptureCapabilityConformance(valid=1)  # type: ignore[arg-type]


def test_coordinator_accepts_complete_and_partial_page_truth(tmp_path: Path) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    manifest = CaptureCapabilityManifest(provider="fake")
    adapter = _CapabilityAdapter(
        manifest,
        (
            CapturePage(
                generation=1,
                events=(_event("complete-1", "item-1", "1"),),
                coverage="complete",
                freshness="fresh",
            ),
        ),
    )
    coordinator.register_adapter("fake", adapter)

    result = coordinator.run(source_id)

    assert result.status == "completed"
    assert coordinator.get_capability_manifest(source_id) == manifest

    partial_coordinator = CaptureCoordinator(
        _store(tmp_path, "partial.sqlite3"), sink=IdempotentFakeSink()
    )
    partial_source = _source(partial_coordinator, provider="partial")
    partial_coordinator.enable(partial_source)
    partial_manifest = CaptureCapabilityManifest(
        provider="partial",
        availability="partial",
        coverage="partial",
        coverage_reason="fixture_scope_excluded",
        freshness="stale",
        health="degraded",
    )
    partial_adapter = _CapabilityAdapter(
        partial_manifest,
        (
            CapturePage(
                generation=1,
                events=(_event("partial-1", "item-2", "1"),),
                coverage="partial",
                freshness="stale",
            ),
        ),
    )
    partial_coordinator.register_adapter("partial", partial_adapter)

    partial_result = partial_coordinator.run(partial_source)

    assert partial_result.status == "completed"
    assert partial_coordinator.capability_conformance(partial_source).warnings == (
        "partial_coverage",
        "freshness_not_current",
    )


def test_fetch_only_adapter_uses_narrow_legacy_compatibility_default(tmp_path: Path) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    adapter = _LegacyFetchOnlyAdapter(
        CapturePage(generation=1, events=(_event("legacy-1", "item-1", "1"),))
    )
    coordinator.register_adapter("fake", adapter)  # type: ignore[arg-type]

    result = coordinator.run(source_id)

    assert result.status == "completed"
    assert adapter.calls == 1
    compatibility = coordinator.get_capability_manifest(source_id)
    assert compatibility.legacy_compatibility is True
    assert compatibility.coverage == "unavailable"
    assert compatibility.network_access == "unknown"
    assert compatibility.data_egress is None
    assert compatibility.connection == "unknown"
    assert {
        "authorization_unknown",
        "connection_unknown",
        "network_posture_unknown",
        "data_egress_unknown",
    }.issubset(set(compatibility.conformance().warnings))


@pytest.mark.parametrize(
    ("manifest", "expected_code"),
    [
        (
            CaptureCapabilityManifest.unavailable("fake", reason="synthetic_outage"),
            "capture_adapter_unavailable",
        ),
        (
            CaptureCapabilityManifest(
                provider="fake",
                availability="partial",
                coverage="partial",
                coverage_reason="reauthorization_fixture",
                authorization="reauthorization_required",
                health="degraded",
            ),
            "capture_reauthorization_required",
        ),
        (
            CaptureCapabilityManifest(
                provider="fake",
                availability="partial",
                coverage="partial",
                coverage_reason="authorization_unknown_fixture",
                authorization="unknown",
                health="degraded",
            ),
            "capture_capability_invalid",
        ),
        (
            CaptureCapabilityManifest(
                provider="fake",
                availability="partial",
                coverage="partial",
                coverage_reason="connection_unknown_fixture",
                connection="unknown",
                health="degraded",
            ),
            "capture_capability_invalid",
        ),
        (
            CaptureCapabilityManifest(
                provider="fake",
                availability="partial",
                coverage="partial",
                coverage_reason="network_unknown_fixture",
                network_access="unknown",
                data_egress=None,
                health="degraded",
            ),
            "capture_capability_invalid",
        ),
        (
            CaptureCapabilityManifest(
                provider="fake",
                availability="partial",
                coverage="partial",
                coverage_reason="disconnect_fixture",
                connection="disconnected",
                health="degraded",
            ),
            "capture_disconnected",
        ),
    ],
)
def test_coordinator_fails_closed_without_adapter_calls(
    tmp_path: Path,
    manifest: CaptureCapabilityManifest,
    expected_code: str,
) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    adapter = _CapabilityAdapter(manifest, (CapturePage(generation=1),))
    coordinator.register_adapter("fake", adapter)

    result = coordinator.run(source_id)

    assert result.status == "skipped"
    assert result.error_code == expected_code
    assert adapter.calls == []
    with coordinator.ledger.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 0


def test_retryable_failure_uses_existing_run_backoff_and_recovers(tmp_path: Path) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    manifest = CaptureCapabilityManifest(
        provider="fake",
        retry_policy=CaptureRetryPolicy(
            retryable_failures=True,
            max_attempts=3,
            backoff=BackoffPolicy(base_seconds=2, max_seconds=30),
            rate_limit=CaptureRateLimitPolicy(mode="retry_after", max_delay_seconds=60),
        ),
    )
    adapter = _CapabilityAdapter(
        manifest,
        (CapturePage(generation=1, events=(_event("retry-1", "item-1", "1"),)),),
        retry_once=True,
    )
    coordinator.register_adapter("fake", adapter)

    failed = coordinator.run(source_id)
    assert failed.error_code == "capture_retryable_failure"
    assert coordinator.get_source(source_id).next_retry_at is not None

    coordinator.resume(source_id)
    recovered = coordinator.run(source_id)

    assert recovered.status == "completed"
    assert recovered.applied_events == 1
    assert len(adapter.calls) == 2


def test_explicit_retry_policy_honors_retry_after_and_attempt_bound(tmp_path: Path) -> None:
    clock = _MutableClock()
    coordinator = CaptureCoordinator(
        _store(tmp_path),
        sink=IdempotentFakeSink(),
        clock=clock,
    )
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    manifest = CaptureCapabilityManifest(
        provider="fake",
        retry_policy=CaptureRetryPolicy(
            retryable_failures=True,
            max_attempts=2,
            backoff=BackoffPolicy(base_seconds=5, max_seconds=30),
            rate_limit=CaptureRateLimitPolicy(mode="retry_after", max_delay_seconds=10),
        ),
    )
    adapter = _CapabilityAdapter(manifest, retry_once=True)
    coordinator.register_adapter("fake", adapter)

    first = coordinator.run(source_id)

    assert first.error_code == "capture_retryable_failure"
    assert coordinator.get_source(source_id).next_retry_at == "2026-01-01T00:00:07.000000Z"

    coordinator.resume(source_id)
    adapter.retry_once = True
    second = coordinator.run(source_id)
    assert second.error_code == "capture_retryable_failure"

    coordinator.resume(source_id)
    exhausted = coordinator.run(source_id)

    assert exhausted.status == "skipped"
    assert exhausted.error_code == "capture_retry_exhausted"
    assert len(adapter.calls) == 2


@pytest.mark.parametrize(
    ("manifest", "page"),
    [
        (
            CaptureCapabilityManifest(
                provider="fake",
                availability="partial",
                coverage="partial",
                coverage_reason="page_truth_fixture",
                freshness="fresh",
                health="degraded",
            ),
            CapturePage(generation=1, coverage="partial", freshness="stale"),
        ),
        (
            CaptureCapabilityManifest(
                provider="fake",
                availability="partial",
                coverage="partial",
                coverage_reason="page_truth_fixture",
                freshness="stale",
                health="degraded",
            ),
            CapturePage(generation=1, coverage="complete", freshness="stale"),
        ),
    ],
)
def test_explicit_page_truth_cannot_contradict_manifest(
    tmp_path: Path,
    manifest: CaptureCapabilityManifest,
    page: CapturePage,
) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    adapter = _CapabilityAdapter(manifest, (page,))
    coordinator.register_adapter("fake", adapter)

    result = coordinator.run(source_id)

    assert result.error_code == "capture_capability_invalid"
    assert adapter.calls == [(None, 0)]
    assert coordinator.ledger._checkpoint(source_id)["cursor"] is None
    with coordinator.ledger.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 0


def test_cursor_declaration_conflict_fails_without_advancing_capture_ledger(tmp_path: Path) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    manifest = CaptureCapabilityManifest(
        provider="fake",
        availability="partial",
        acquisition_mode="initial_snapshot",
        initial_snapshot=True,
        incremental=False,
        cursor_support=False,
        coverage="partial",
        coverage_reason="snapshot_only_fixture",
        health="degraded",
    )
    adapter = _CapabilityAdapter(
        manifest,
        (CapturePage(generation=1, next_cursor="unexpected-cursor", done=False),),
    )
    coordinator.register_adapter("fake", adapter)

    result = coordinator.run(source_id)

    assert result.error_code == "capture_capability_invalid"
    assert coordinator.ledger._checkpoint(source_id)["cursor"] is None
    with coordinator.ledger.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0] == 0


def test_restart_delete_and_replay_reuse_existing_capture_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sink = IdempotentFakeSink()
    coordinator = CaptureCoordinator(store, sink=sink)
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    manifest = CaptureCapabilityManifest(
        provider="fake",
        source_deletion="coordinated",
        purge_coordination="coordinated",
    )
    pages = (
        CapturePage(
            generation=1,
            events=(_event("delete-1", "item-1", "1"),),
            next_cursor="synthetic-next",
            done=False,
        ),
        CapturePage(
            generation=1,
            page_order=1,
            events=(_event("delete-2", "item-1", "2", operation="delete"),),
        ),
    )
    adapter = _CapabilityAdapter(manifest, pages)
    coordinator.register_adapter("fake", adapter)
    first = coordinator.run(source_id)
    assert first.status == "completed"
    assert coordinator.get_capability_manifest(source_id).purge_coordination == "coordinated"

    restarted = CaptureCoordinator(CoreStore(store.database_path), sink=sink)
    replay_adapter = _CapabilityAdapter(manifest, pages)
    restarted.register_adapter("fake", replay_adapter)
    replay = restarted.run(source_id)

    assert replay.status == "completed"
    assert replay.duplicate_events == 2
    assert len(sink.calls) == 2
    with restarted.ledger.store.connect() as connection:
        item = connection.execute(
            "SELECT item_state FROM capture_items WHERE source_id=? AND provider_item_id=?",
            (source_id, "item-1"),
        ).fetchone()
    assert item is not None and item["item_state"] == "deleted"
