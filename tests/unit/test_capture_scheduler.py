"""Focused deterministic acceptance for the disabled-by-default scheduler seam."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from allthecontext.capture import (
    BackoffPolicy,
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureEvent,
    CapturePage,
    CaptureRateLimitPolicy,
    CaptureRetryableError,
    CaptureRetryPolicy,
    CaptureRunResult,
    DeterministicFakeAdapter,
    IdempotentFakeSink,
)
from allthecontext.capture_scheduler import (
    CaptureScheduler,
    ConnectorLimits,
    SchedulerConfig,
)
from allthecontext.storage import CoreStore


def _store(tmp_path: Path, name: str = "core.sqlite3") -> CoreStore:
    store = CoreStore(tmp_path / name)
    store.initialize_vault()
    return store


def _source(coordinator: CaptureCoordinator, *, provider: str = "fake") -> str:
    source = coordinator.create_source(
        provider=provider,
        account_label="synthetic-scheduler-account",
        account_fingerprint="scheduler-fingerprint-1",
        local_only_acknowledged=True,
    )
    return cast(str, source.id)


def _event(event_id: str = "scheduler-event-1") -> CaptureEvent:
    return CaptureEvent(
        provider_event_id=event_id,
        provider_item_id="scheduler-item-1",
        order_key="1",
        payload={"fixture": "scheduler"},
        generation=1,
    )


def _page(event: CaptureEvent | None = None) -> CapturePage:
    return CapturePage(
        generation=1,
        events=() if event is None else (event,),
        done=True,
    )


class _MutableClock:
    def __init__(self, value: str = "2026-01-01T00:00:00.000000Z") -> None:
        self.value: str = value

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


class _RetryAdapter:
    def __init__(
        self,
        manifest: CaptureCapabilityManifest,
        page: CapturePage,
        *,
        failures: int = 0,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.capability_manifest = manifest
        self.page = page
        self.failures = failures
        self.retry_after_seconds = retry_after_seconds
        self.calls: list[tuple[str | None, int]] = []

    def fetch_page(
        self,
        source: Any,
        cursor: str | None,
        page_order: int,
    ) -> CapturePage:
        del source
        self.calls.append((cursor, page_order))
        if self.failures:
            self.failures -= 1
            raise CaptureRetryableError(self.retry_after_seconds)
        return self.page


def _enable(coordinator: CaptureCoordinator, source_id: str) -> None:
    coordinator.enable(source_id)


def test_scheduler_is_disabled_by_default_and_uses_injected_sleeper(tmp_path: Path) -> None:
    clock = _MutableClock()
    coordinator = CaptureCoordinator(
        _store(tmp_path),
        clock=clock,
        sink=IdempotentFakeSink(),
    )
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    adapter = DeterministicFakeAdapter((_page(_event()),))
    coordinator.register_adapter("fake", adapter)

    disabled = CaptureScheduler(coordinator, clock=clock)
    disabled_report = disabled.run_once()
    assert disabled.enabled is False
    assert disabled_report.plan.enabled is False
    assert disabled_report.dispatched == ()
    assert adapter.calls == []

    sleeps: list[float] = []
    enabled = CaptureScheduler(
        coordinator,
        config=SchedulerConfig(enabled=True, poll_interval_seconds=17),
        clock=clock,
        sleeper=sleeps.append,
    )
    reports = enabled.run_forever(max_cycles=2)
    assert reports[0].dispatched[0].kind == "initial_backfill"
    assert reports[0].results[0].status == "completed"
    assert reports[1].dispatched == ()
    assert sleeps == [17.0]


def test_initial_backfill_then_incremental_due_time_uses_existing_checkpoint(
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    coordinator = CaptureCoordinator(
        _store(tmp_path),
        clock=clock,
        sink=IdempotentFakeSink(),
    )
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    adapter = DeterministicFakeAdapter((_page(_event()),))
    coordinator.register_adapter("fake", adapter)
    scheduler = CaptureScheduler(
        coordinator,
        config=SchedulerConfig(enabled=True, incremental_interval_seconds=60),
        clock=clock,
    )

    initial = scheduler.run_once()
    assert initial.dispatched[0].kind == "initial_backfill"
    checkpoint = coordinator.ledger._checkpoint(source_id)
    assert checkpoint["last_order_key"] == "1"
    assert checkpoint["cursor"] is None

    clock.advance(59)
    assert scheduler.plan().entries == ()
    clock.advance(1)
    due = scheduler.plan()
    assert len(due.entries) == 1
    assert due.entries[0].kind == "incremental"

    incremental = scheduler.run_once()
    assert incremental.results[0].duplicate_events == 1
    assert adapter.calls == [(None, 0), (None, 0)]
    assert coordinator.ledger._checkpoint(source_id)["last_order_key"] == "1"


def test_scheduler_restart_reuses_persisted_coordinator_state_without_scheduler_ledger(
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    database_path = tmp_path / "restart.sqlite3"
    first = CaptureCoordinator(
        CoreStore(database_path),
        clock=clock,
        sink=IdempotentFakeSink(),
    )
    first.ledger.store.initialize_vault()
    source_id = _source(first)
    _enable(first, source_id)
    first_adapter = DeterministicFakeAdapter((_page(_event()),))
    first.register_adapter("fake", first_adapter)
    first_scheduler = CaptureScheduler(
        first,
        config=SchedulerConfig(enabled=True, incremental_interval_seconds=30),
        clock=clock,
    )
    first_scheduler.run_once()
    persisted = first.ledger._checkpoint(source_id)

    restarted_store = CoreStore(database_path)
    restarted = CaptureCoordinator(
        restarted_store,
        clock=clock,
        sink=IdempotentFakeSink(),
    )
    restarted_adapter = DeterministicFakeAdapter((_page(_event()),))
    restarted.register_adapter("fake", restarted_adapter)
    restarted_scheduler = CaptureScheduler(
        restarted,
        config=SchedulerConfig(enabled=True, incremental_interval_seconds=30),
        clock=clock,
    )

    assert restarted.ledger._checkpoint(source_id) == persisted
    clock.advance(29)
    assert restarted_scheduler.plan().entries == ()
    clock.advance(1)
    assert restarted_scheduler.plan().entries[0].kind == "incremental"
    restarted_scheduler.run_once()
    restarted_checkpoint = restarted.ledger._checkpoint(source_id)
    assert {
        key: restarted_checkpoint[key]
        for key in ("generation", "last_order_key", "last_event_id", "cursor")
    } == {
        key: persisted[key] for key in ("generation", "last_order_key", "last_event_id", "cursor")
    }
    with restarted.ledger.store.connect() as connection:
        scheduler_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%scheduler%'"
        ).fetchall()
    assert scheduler_tables == []


def test_scheduler_honors_retry_after_and_existing_bounded_exponential_backoff(
    tmp_path: Path,
) -> None:
    clock = _MutableClock()
    coordinator = CaptureCoordinator(
        _store(tmp_path),
        clock=clock,
        sink=IdempotentFakeSink(),
    )
    source_id = _source(coordinator)
    _enable(coordinator, source_id)
    retry_after_manifest = CaptureCapabilityManifest(
        provider="fake",
        retry_policy=CaptureRetryPolicy(
            max_attempts=2,
            backoff=BackoffPolicy(base_seconds=2, max_seconds=30),
            rate_limit=CaptureRateLimitPolicy(mode="retry_after", max_delay_seconds=60),
        ),
    )
    retry_after_adapter = _RetryAdapter(
        retry_after_manifest,
        _page(_event("retry-after-event")),
        failures=1,
        retry_after_seconds=7,
    )
    coordinator.register_adapter("fake", retry_after_adapter)
    scheduler = CaptureScheduler(
        coordinator,
        config=SchedulerConfig(enabled=True),
        clock=clock,
    )

    failed = scheduler.run_once()
    assert failed.results[0].error_code == "capture_retryable_failure"
    assert coordinator.get_source(source_id).next_retry_at == "2026-01-01T00:00:07.000000Z"
    clock.advance(6)
    assert scheduler.plan().entries == ()
    clock.advance(1)
    recovered = scheduler.run_once()
    assert recovered.dispatched[0].kind == "retry"
    assert recovered.results[0].status == "completed"

    second_source = _source(coordinator)
    _enable(coordinator, second_source)
    exponential_manifest = CaptureCapabilityManifest(
        provider="fake",
        retry_policy=CaptureRetryPolicy(
            max_attempts=4,
            backoff=BackoffPolicy(base_seconds=2, max_seconds=5),
            rate_limit=CaptureRateLimitPolicy(mode="bounded_backoff", max_delay_seconds=60),
        ),
    )
    exponential_adapter = _RetryAdapter(
        exponential_manifest,
        _page(_event("exponential-event")),
        failures=3,
    )
    coordinator.register_adapter("fake", exponential_adapter)

    first_failure = scheduler.run_once()
    assert first_failure.results[0].error_code == "capture_retryable_failure"
    assert coordinator.get_source(second_source).next_retry_at == "2026-01-01T00:00:09.000000Z"
    assert scheduler.plan().entries == ()
    clock.advance(2)
    second_failure = scheduler.run_once()
    assert second_failure.results[0].error_code == "capture_retryable_failure"
    assert coordinator.get_source(second_source).next_retry_at == "2026-01-01T00:00:13.000000Z"
    clock.advance(3)
    assert scheduler.plan().entries == ()
    clock.advance(1)
    third_failure = scheduler.run_once()
    assert third_failure.results[0].error_code == "capture_retryable_failure"
    assert coordinator.get_source(second_source).next_retry_at == "2026-01-01T00:00:18.000000Z"
    clock.advance(4)
    assert scheduler.plan().entries == ()
    clock.advance(1)
    final = scheduler.run_once()
    assert final.results[0].status == "completed"
    assert coordinator.get_source(second_source).retry_count == 0
    assert coordinator.get_source(second_source).next_retry_at is None


def test_health_deduplicates_reauthorization_and_is_silent_when_healthy(
    tmp_path: Path,
) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    first_source = _source(coordinator)
    second_source = _source(coordinator)
    _enable(coordinator, first_source)
    _enable(coordinator, second_source)
    reauth_manifest = CaptureCapabilityManifest(
        provider="fake",
        availability="partial",
        coverage="partial",
        coverage_reason="reauthorization_fixture",
        authorization="reauthorization_required",
        health="degraded",
    )
    adapter = _RetryAdapter(reauth_manifest, _page())
    coordinator.register_adapter("fake", adapter)
    scheduler = CaptureScheduler(
        coordinator,
        config=SchedulerConfig(enabled=True),
    )

    first = scheduler.run_once()
    assert first.dispatched == ()
    assert len(first.health.reauthorization_required) == 1
    assert len(first.health.actions) == 1
    assert first.health.actions[0].provider == "fake"
    assert set(first.health.actions[0].source_ids) == {first_source, second_source}
    second = scheduler.run_once()
    assert second.health.reauthorization_required == first.health.reauthorization_required
    assert second.health.actions == ()

    adapter.capability_manifest = CaptureCapabilityManifest(provider="fake")
    healthy = scheduler.health()
    assert healthy.state == "healthy"
    assert healthy.reauthorization_required == ()
    assert healthy.actions == ()


def test_per_connector_concurrency_and_resource_bounds_defer_due_work(
    tmp_path: Path,
) -> None:
    coordinator = CaptureCoordinator(_store(tmp_path), sink=IdempotentFakeSink())
    source_ids = [_source(coordinator) for _ in range(3)]
    for source_id in source_ids:
        _enable(coordinator, source_id)
    coordinator.register_adapter("fake", DeterministicFakeAdapter((_page(),)))

    calls: list[str] = []

    def runner(source_id: str) -> CaptureRunResult:
        calls.append(source_id)
        return CaptureRunResult(
            run_id=f"synthetic-run-{source_id}",
            source_id=source_id,
            status="completed",
            error_code=None,
            pages=0,
            events=0,
            applied_events=0,
            duplicate_events=0,
            failures=0,
            retry_count=0,
            lag_events=0,
            lag_pages=0,
        )

    scheduler = CaptureScheduler(
        coordinator,
        config=SchedulerConfig(enabled=True, max_workers=4),
        connector_limits={
            "fake": ConnectorLimits(
                max_concurrency=3,
                max_resource_units=4,
                resource_units_per_run=2,
            )
        },
        runner=runner,
    )

    report = scheduler.run_once()
    assert len(report.plan.entries) == 3
    assert len(report.dispatched) == 2
    assert len(report.deferred) == 1
    assert all(entry.resource_units == 2 for entry in report.dispatched)
    assert len(calls) == 2
