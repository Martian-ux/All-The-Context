"""Disabled-by-default scheduling around the Core capture coordinator.

This module is intentionally an orchestration seam, not another capture
authority.  The coordinator remains responsible for leases, lifecycle
transitions, replay/idempotency, checkpoints, and persisted retry metadata.
The scheduler only reads those projections, chooses due work, and invokes the
coordinator.  It keeps no cursor, event ledger, lease, or durable notification
state.

Core productization owns one non-daemon interruptible thread, a machine-local
enablement sidecar, and explicit process gates. It reuses this planner plus the
shared capture runtime adapter refresh; it does not fork coordinator, sink, or
adapter logic.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Literal

from .capture import (
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureError,
    CaptureRunResult,
    CaptureSource,
)
from .capture_runtime import (
    read_scheduler_durable_state,
    refresh_local_workspace_adapter,
    write_scheduler_enabled,
)
from .config import CoreConfig

type SchedulerClock = Callable[[], str]
type SchedulerSleeper = Callable[[float], None]
type CaptureRunner = Callable[[str], CaptureRunResult]
type ResourceCost = Callable[[CaptureSource], int]
ScheduleKind = Literal["initial_backfill", "incremental", "retry"]
HealthState = Literal["healthy", "degraded", "unavailable"]
CAPTURE_SCHEDULER_ENABLED_ENV = "ATC_CAPTURE_SCHEDULER_ENABLED"
UPDATE_HEALTH_OPERATION_ENV = "ATC_UPDATE_HEALTH_OPERATION"
_CORE_SCHEDULER_JOIN_TIMEOUT_SECONDS = 8.0
_CORE_SCHEDULER_JOIN_POLL_SECONDS = 0.05
_CORE_SCHEDULER_THREAD_NAME = "atc-capture-scheduler"


def _merge_health(left: HealthState, right: HealthState) -> HealthState:
    if "unavailable" in {left, right}:
        return "unavailable"
    if "degraded" in {left, right}:
        return "degraded"
    return "healthy"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("capture scheduler clock must be timezone-aware")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ConnectorLimits:
    """Per-provider bounds applied to one scheduler cycle."""

    max_concurrency: int = 1
    max_resource_units: int = 1
    resource_units_per_run: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.max_concurrency) is not int
            or type(self.max_resource_units) is not int
            or type(self.resource_units_per_run) is not int
            or self.max_concurrency < 1
            or self.max_resource_units < 1
            or not 1 <= self.resource_units_per_run <= self.max_resource_units
        ):
            raise ValueError("invalid capture scheduler connector limits")


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Bounded scheduler configuration; scheduling is opt-in by default."""

    enabled: bool = False
    poll_interval_seconds: int = 60
    incremental_interval_seconds: int = 300
    max_sources_per_cycle: int = 500
    max_source_pages_per_cycle: int = 1
    max_health_pages: int = 4
    max_workers: int = 4
    default_connector_limits: ConnectorLimits = field(default_factory=ConnectorLimits)

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or type(self.poll_interval_seconds) is not int
            or type(self.incremental_interval_seconds) is not int
            or type(self.max_sources_per_cycle) is not int
            or type(self.max_source_pages_per_cycle) is not int
            or type(self.max_health_pages) is not int
            or type(self.max_workers) is not int
            or self.poll_interval_seconds < 1
            or self.incremental_interval_seconds < 1
            or not 1 <= self.max_sources_per_cycle <= 500
            or not 1 <= self.max_source_pages_per_cycle <= 64
            or not 1 <= self.max_health_pages <= 64
            or not 1 <= self.max_workers <= 32
            or not isinstance(self.default_connector_limits, ConnectorLimits)
        ):
            raise ValueError("invalid capture scheduler configuration")


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    source_id: str
    provider: str
    kind: ScheduleKind
    resource_units: int


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    enabled: bool
    entries: tuple[ScheduleEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class _SourceBatch:
    sources: tuple[CaptureSource, ...]
    total: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ReauthorizationState:
    """One bounded actionable state for all affected sources of a provider."""

    provider: str
    source_ids: tuple[str, ...]
    reason_code: Literal["capture_reauthorization_required"] = "capture_reauthorization_required"
    action: Literal["reauthorize"] = "reauthorize"


@dataclass(frozen=True, slots=True)
class HealthAction:
    code: Literal["capture_reauthorization_required"]
    provider: str
    source_ids: tuple[str, ...]
    action: Literal["reauthorize"] = "reauthorize"


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    provider: str
    state: HealthState
    source_ids: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    reauthorization: ReauthorizationState | None = None


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    state: HealthState
    connectors: tuple[ConnectorHealth, ...] = ()
    reauthorization_required: tuple[ReauthorizationState, ...] = ()
    actions: tuple[HealthAction, ...] = ()
    source_total: int = 0
    inspected_source_count: int = 0
    truncated: bool = False
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SchedulerRunReport:
    plan: SchedulePlan
    dispatched: tuple[ScheduleEntry, ...] = ()
    deferred: tuple[ScheduleEntry, ...] = ()
    results: tuple[CaptureRunResult, ...] = ()
    health: HealthSnapshot = field(default_factory=lambda: HealthSnapshot("healthy"))

    @property
    def scheduled(self) -> tuple[ScheduleEntry, ...]:
        """Compatibility spelling for callers that call dispatch a schedule."""

        return self.dispatched


# The module is explicitly experimental; this alias makes the ownership
# boundary clear without adding a public package export or a stable ABI claim.
ConnectorResourceBounds = ConnectorLimits


class CaptureScheduler:
    """Select and execute due coordinator runs without owning capture state."""

    def __init__(
        self,
        coordinator: CaptureCoordinator,
        *,
        config: SchedulerConfig | None = None,
        clock: SchedulerClock | None = None,
        sleeper: SchedulerSleeper = sleep,
        runner: CaptureRunner | None = None,
        connector_limits: Mapping[str, ConnectorLimits] | None = None,
        resource_cost: ResourceCost | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.config = config or SchedulerConfig()
        self.clock = clock or coordinator.clock
        self.sleeper = sleeper
        self.runner = runner or coordinator.run
        self.connector_limits = dict(connector_limits or {})
        self.resource_cost = resource_cost
        self._enabled = self.config.enabled
        # This is an ephemeral selection rotation only.  It is never written
        # to Core and cannot affect capture replay, cursor, or checkpoint
        # authority.
        self._source_rotation_offset = 0
        self._announced_reauthorization: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        """Enable this in-memory scheduler instance explicitly."""

        self._enabled = True

    def disable(self) -> None:
        """Stop future dispatches; an in-flight coordinator run owns its lease."""

        self._enabled = False

    def _limits_for(self, provider: str) -> ConnectorLimits:
        return self.connector_limits.get(provider, self.config.default_connector_limits)

    def _sources(self, *, full_scan: bool = False) -> _SourceBatch:
        """Read bounded source pages, rotating selection but not Core state."""

        page_size = self.config.max_sources_per_cycle
        page_limit = (
            self.config.max_health_pages if full_scan else self.config.max_source_pages_per_cycle
        )
        if full_scan:
            first_page, total = self.coordinator.list_sources(limit=page_size, offset=0)
            start_offset = 0
        else:
            _probe, total = self.coordinator.list_sources(limit=1, offset=0)
            if total == 0:
                return _SourceBatch((), 0, False)
            start_offset = self._source_rotation_offset % total
            first_page, _ignored_total = self.coordinator.list_sources(
                limit=page_size,
                offset=start_offset,
            )

        pages_read = 1
        seen: dict[str, CaptureSource] = {source.id: source for source in first_page}
        while first_page and len(seen) < total and pages_read < page_limit:
            offset = (start_offset + pages_read * page_size) % total
            first_page, _ignored_total = self.coordinator.list_sources(
                limit=page_size,
                offset=offset,
            )
            pages_read += 1
            for source in first_page:
                seen.setdefault(source.id, source)

        if not full_scan and total:
            self._source_rotation_offset = (start_offset + pages_read * page_size) % total
        return _SourceBatch(
            sources=tuple(seen.values()),
            total=total,
            truncated=len(seen) < total,
        )

    @staticmethod
    def _last_run_completed(status: Mapping[str, object]) -> bool:
        last_run = status.get("last_run")
        return isinstance(last_run, Mapping) and last_run.get("state") == "completed"

    @staticmethod
    def _manifest_allows_scheduling(manifest: CaptureCapabilityManifest) -> bool:
        return (
            manifest.availability != "unavailable"
            and manifest.health != "unavailable"
            and manifest.authorization
            not in {"reauthorization_required", "unauthorized", "unknown"}
            and manifest.connection == "connected"
        )

    def _retry_is_due(
        self,
        source: CaptureSource,
        manifest: CaptureCapabilityManifest,
        now: datetime,
    ) -> bool:
        if source.next_retry_at is None or not self._manifest_allows_scheduling(manifest):
            return False
        if not manifest.retry_policy.retryable_failures:
            return False
        if source.retry_count >= manifest.retry_policy.max_attempts:
            return False
        try:
            return _parse_time(source.next_retry_at) <= now
        except ValueError:
            return False

    def _due_kind(
        self,
        source: CaptureSource,
        manifest: CaptureCapabilityManifest,
        now: datetime,
    ) -> ScheduleKind | None:
        if source.lifecycle_state not in {"enabled", "degraded"}:
            return None
        if not self._manifest_allows_scheduling(manifest):
            return None
        if self._retry_is_due(source, manifest, now):
            return "retry"
        if (
            source.lifecycle_state == "degraded"
            and source.last_error_code == "capture_lease_expired"
            and source.next_retry_at is None
            and manifest.retry_policy.retryable_failures
            and source.retry_count < manifest.retry_policy.max_attempts
        ):
            return "retry"
        if source.lifecycle_state != "enabled" or source.next_retry_at is not None:
            return None

        status = self.coordinator.status(source.id)
        completed = self._last_run_completed(status)
        if manifest.initial_snapshot and not completed:
            return "initial_backfill"
        if not manifest.incremental:
            return None
        if not completed or source.last_run_at is None:
            return "incremental"
        try:
            last_run = _parse_time(source.last_run_at)
        except ValueError:
            return None
        if (now - last_run).total_seconds() >= self.config.incremental_interval_seconds:
            return "incremental"
        return None

    def plan(self) -> SchedulePlan:
        """Read Core projections and return due work without mutating state."""

        if not self.enabled:
            return SchedulePlan(enabled=False)
        now = _parse_time(self.clock())
        entries: list[ScheduleEntry] = []
        for source in self._sources().sources:
            if source.lifecycle_state not in {"enabled", "degraded"}:
                continue
            try:
                manifest = self.coordinator.capability_manifest(source.id)
            except CaptureError:
                continue
            kind = self._due_kind(source, manifest, now)
            if kind is None:
                continue
            limits = self._limits_for(source.provider)
            try:
                resource_units = (
                    self.resource_cost(source)
                    if self.resource_cost is not None
                    else limits.resource_units_per_run
                )
            except Exception:
                continue
            if (
                type(resource_units) is not int
                or not 1 <= resource_units <= limits.max_resource_units
            ):
                continue
            entries.append(
                ScheduleEntry(
                    source_id=source.id,
                    provider=source.provider,
                    kind=kind,
                    resource_units=resource_units,
                )
            )
        return SchedulePlan(enabled=True, entries=tuple(entries))

    def _select(
        self, entries: tuple[ScheduleEntry, ...]
    ) -> tuple[tuple[ScheduleEntry, ...], tuple[ScheduleEntry, ...]]:
        selected: list[ScheduleEntry] = []
        deferred: list[ScheduleEntry] = []
        concurrency: defaultdict[str, int] = defaultdict(int)
        resources: defaultdict[str, int] = defaultdict(int)
        for entry in entries:
            if len(selected) >= self.config.max_workers:
                deferred.append(entry)
                continue
            limits = self._limits_for(entry.provider)
            if (
                concurrency[entry.provider] >= limits.max_concurrency
                or resources[entry.provider] + entry.resource_units > limits.max_resource_units
            ):
                deferred.append(entry)
                continue
            selected.append(entry)
            concurrency[entry.provider] += 1
            resources[entry.provider] += entry.resource_units
        return tuple(selected), tuple(deferred)

    def _resume_retry(self, source_id: str) -> None:
        source = self.coordinator.get_source(source_id)
        if source.lifecycle_state == "degraded":
            # The coordinator owns the transition and will re-check its lease
            # and current source state before any run-owned mutation.
            self.coordinator.resume(source_id)

    def _execute(self, entry: ScheduleEntry) -> CaptureRunResult:
        try:
            if entry.kind == "retry":
                self._resume_retry(entry.source_id)
            return self.runner(entry.source_id)
        except CaptureError as error:
            return self.coordinator.ledger.skipped_result(entry.source_id, error.code)
        except Exception:
            return self.coordinator.ledger.skipped_result(entry.source_id, "capture_failed")

    def _health_from_sources(
        self,
        sources: tuple[CaptureSource, ...],
        *,
        source_total: int,
        truncated: bool,
        emit_actions: bool = True,
    ) -> HealthSnapshot:
        grouped: dict[
            str, list[tuple[CaptureSource, CaptureCapabilityManifest | None, str | None]]
        ] = defaultdict(list)
        for source in sources:
            if source.lifecycle_state not in {"enabled", "degraded", "reconciling"}:
                continue
            manifest: CaptureCapabilityManifest | None = None
            capability_error: str | None = None
            try:
                manifest = self.coordinator.capability_manifest(source.id)
            except CaptureError as error:
                capability_error = error.code
            grouped[source.provider].append((source, manifest, capability_error))

        connectors: list[ConnectorHealth] = []
        reauthorization: list[ReauthorizationState] = []
        for provider in sorted(grouped):
            records = grouped[provider]
            source_ids = tuple(record[0].id for record in records)
            reasons: set[str] = set()
            state: HealthState = "healthy"
            reauth_source_ids: list[str] = []
            for source, manifest, capability_error in records:
                if source.last_error_code is not None:
                    reasons.add(source.last_error_code)
                    state = _merge_health(state, "degraded")
                if capability_error is not None:
                    reasons.add(capability_error)
                    state = _merge_health(state, "unavailable")
                    continue
                if manifest is None:
                    reasons.add("capture_capability_invalid")
                    state = _merge_health(state, "unavailable")
                    continue
                if manifest.authorization == "reauthorization_required":
                    reauth_source_ids.append(source.id)
                    reasons.add("capture_reauthorization_required")
                    state = _merge_health(state, "degraded")
                elif manifest.authorization in {"unauthorized", "unknown"}:
                    reasons.add("capture_authorization_unavailable")
                    state = _merge_health(state, "degraded")
                if manifest.connection in {"disconnected", "unknown"}:
                    reasons.add("capture_disconnected")
                    connection_state: HealthState = (
                        "unavailable" if manifest.connection == "disconnected" else "degraded"
                    )
                    state = _merge_health(state, connection_state)
                if manifest.availability == "unavailable" or manifest.health == "unavailable":
                    reasons.add("capture_adapter_unavailable")
                    state = _merge_health(state, "unavailable")
                elif manifest.health == "degraded" or manifest.coverage != "complete":
                    state = _merge_health(state, "degraded")
                    if manifest.coverage != "complete":
                        reasons.add("partial_coverage")
                if manifest.freshness in {"stale", "unknown"}:
                    state = _merge_health(state, "degraded")
                    reasons.add("stale_freshness")
            reauth = None
            if reauth_source_ids:
                reauth = ReauthorizationState(provider, tuple(sorted(reauth_source_ids)))
                reauthorization.append(reauth)
            connectors.append(
                ConnectorHealth(
                    provider=provider,
                    state=state,
                    source_ids=source_ids,
                    reason_codes=tuple(sorted(reasons)),
                    reauthorization=reauth,
                )
            )

        new_actions: list[HealthAction] = []
        if emit_actions:
            current_providers = {state.provider for state in reauthorization}
            for reauth_state in reauthorization:
                if reauth_state.provider not in self._announced_reauthorization:
                    new_actions.append(
                        HealthAction(
                            code=reauth_state.reason_code,
                            provider=reauth_state.provider,
                            source_ids=reauth_state.source_ids,
                        )
                    )
            self._announced_reauthorization.intersection_update(current_providers)
            self._announced_reauthorization.update(current_providers)

        overall: HealthState = "healthy"
        if any(connector.state == "degraded" for connector in connectors):
            overall = "degraded"
        if any(connector.state == "unavailable" for connector in connectors):
            overall = "unavailable"
        reason_codes: tuple[str, ...] = ()
        if truncated:
            overall = _merge_health(overall, "degraded")
            reason_codes = ("capture_health_truncated",)
        return HealthSnapshot(
            state=overall,
            connectors=tuple(connectors),
            reauthorization_required=tuple(reauthorization),
            actions=tuple(new_actions),
            source_total=source_total,
            inspected_source_count=len(sources),
            truncated=truncated,
            reason_codes=reason_codes,
        )

    def health(self, *, consume_actions: bool = True) -> HealthSnapshot:
        """Aggregate content-free health.

        Status and other read-only callers must pass ``consume_actions=False`` so
        one-shot reauthorization actions are not consumed and rotation is not
        advanced. Full-scan inspection does not mutate the selection offset.
        """

        batch = self._sources(full_scan=True)
        return self._health_from_sources(
            batch.sources,
            source_total=batch.total,
            truncated=batch.truncated,
            emit_actions=consume_actions,
        )

    def run_once(self) -> SchedulerRunReport:
        """Dispatch one bounded cycle; no real-time waiting occurs here."""

        if self.enabled:
            self.coordinator.ledger.recover_expired_runs()
        plan = self.plan()
        if not plan.enabled:
            batch = self._sources(full_scan=True)
            return SchedulerRunReport(
                plan=plan,
                health=self._health_from_sources(
                    batch.sources,
                    source_total=batch.total,
                    truncated=batch.truncated,
                    emit_actions=False,
                ),
            )
        selected, deferred = self._select(plan.entries)
        results: list[CaptureRunResult] = []
        if selected:
            if self.config.max_workers == 1:
                results.extend(self._execute(entry) for entry in selected)
            else:
                with ThreadPoolExecutor(
                    max_workers=len(selected), thread_name_prefix="atc-capture"
                ) as pool:
                    futures = [pool.submit(self._execute, entry) for entry in selected]
                    results.extend(future.result() for future in futures)
        return SchedulerRunReport(
            plan=plan,
            dispatched=selected,
            deferred=deferred,
            results=tuple(results),
            health=self.health(),
        )

    def run_forever(self, *, max_cycles: int | None = None) -> tuple[SchedulerRunReport, ...]:
        """Run with an injected sleeper; bounded tests can supply max_cycles."""

        if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 0):
            raise ValueError("invalid capture scheduler cycle bound")
        reports: list[SchedulerRunReport] = []
        while self.enabled and (max_cycles is None or len(reports) < max_cycles):
            reports.append(self.run_once())
            if max_cycles is not None and len(reports) >= max_cycles:
                break
            if self.enabled:
                self.sleeper(float(self.config.poll_interval_seconds))
        return tuple(reports)


def scheduler_process_gate_open() -> bool:
    """Return True only for the exact process enablement value ``1``."""

    return os.environ.get(CAPTURE_SCHEDULER_ENABLED_ENV) == "1"


def scheduler_update_health_forced_off() -> bool:
    """Installer/update health probes must not start capture scheduling.

    Any presence of ``ATC_UPDATE_HEALTH_OPERATION``, including the empty string,
    force-disables the scheduler.
    """

    return UPDATE_HEALTH_OPERATION_ENV in os.environ


def capture_scheduler_status_payload(
    data_dir: Path,
    *,
    running: bool | None = None,
) -> dict[str, Any]:
    """Content-free scheduler status from env gates and the durable sidecar.

    ``running`` is included only when the caller can observe the Core worker.
    Contributor CLI must omit it rather than report a false stopped state.
    """

    durable = read_scheduler_durable_state(data_dir)
    process_gate = scheduler_process_gate_open()
    forced_off = scheduler_update_health_forced_off()
    dispatch_allowed = process_gate and durable.valid and durable.enabled and not forced_off
    if forced_off:
        reason_code = "forced_off"
    elif not durable.valid:
        reason_code = "invalid_config"
    elif not process_gate:
        reason_code = "process_gate_closed"
    elif not durable.enabled:
        reason_code = "disabled"
    else:
        reason_code = "enabled"
    payload: dict[str, Any] = {
        "config_valid": durable.valid,
        "dispatch_allowed": dispatch_allowed,
        "durable_enabled": durable.enabled and durable.valid,
        "enabled": dispatch_allowed,
        "max_workers": 1,
        "process_gate": process_gate,
        "reason_code": reason_code,
        "update_health_forced_off": forced_off,
    }
    if running is not None:
        payload["running"] = running
    return payload


class CoreCaptureScheduler:
    """Core-owned opt-in scheduler thread around the shared coordinator."""

    def __init__(
        self,
        coordinator: CaptureCoordinator,
        config: CoreConfig,
        *,
        scheduler_config: SchedulerConfig | None = None,
        clock: SchedulerClock | None = None,
        join_timeout_seconds: float = _CORE_SCHEDULER_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        self.coordinator = coordinator
        self.config = config
        product_config = scheduler_config or SchedulerConfig()
        self._scheduler = CaptureScheduler(
            coordinator,
            config=SchedulerConfig(
                enabled=False,
                poll_interval_seconds=product_config.poll_interval_seconds,
                incremental_interval_seconds=product_config.incremental_interval_seconds,
                max_sources_per_cycle=product_config.max_sources_per_cycle,
                max_source_pages_per_cycle=product_config.max_source_pages_per_cycle,
                max_health_pages=product_config.max_health_pages,
                max_workers=1,
                default_connector_limits=product_config.default_connector_limits,
            ),
            clock=clock or coordinator.clock,
        )
        self._join_timeout_seconds = float(join_timeout_seconds)
        self._control_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._closing = threading.Event()
        self._thread: threading.Thread | None = None

    def dispatch_allowed(self) -> bool:
        if scheduler_update_health_forced_off() or not scheduler_process_gate_open():
            return False
        durable = read_scheduler_durable_state(self.config.data_dir)
        return durable.valid and durable.enabled

    def status(self) -> dict[str, Any]:
        """Content-free scheduler status that does not mutate scheduling state."""

        with self._lifecycle_lock:
            thread = self._thread
            running = thread is not None and thread.is_alive()
        return capture_scheduler_status_payload(
            self.config.data_dir,
            running=running,
        )

    def enable(self) -> dict[str, Any]:
        with self._control_lock:
            write_scheduler_enabled(self.config.data_dir, enabled=True)
            self._start_unlocked()
        return self.status()

    def disable(self) -> dict[str, Any]:
        with self._control_lock:
            write_scheduler_enabled(self.config.data_dir, enabled=False)
            thread = self._signal_stop_unlocked()
        self._join_worker(thread, timeout=self._join_timeout_seconds)
        return self.status()

    def start(self) -> None:
        """Start the non-daemon loop after Core is ready. Idempotent."""

        with self._control_lock:
            self._start_unlocked()

    def stop(self) -> None:
        """Signal stop and join with a bound. In-flight work completes. Idempotent."""

        with self._control_lock:
            thread = self._signal_stop_unlocked()
        self._join_worker(thread, timeout=self._join_timeout_seconds)

    def shutdown(self) -> None:
        """Signal stop and wait until the captured worker is dead. Idempotent.

        Sets a permanent closing fence for this instance. Later enable/start
        cannot clear stop or revive the worker. This does not cancel in-flight
        coordinator work. Prompt admin disable stays on ``stop`` / ``disable``.
        """

        with self._control_lock:
            thread = self._begin_shutdown_unlocked()
        if thread is not None:
            thread.join()
        self._clear_dead_thread()

    def _start_unlocked(self) -> None:
        if scheduler_update_health_forced_off() or not self.dispatch_allowed():
            return
        spawned: threading.Thread | None = None
        with self._lifecycle_lock:
            if self._closing.is_set():
                return
            thread = self._thread
            if thread is not None and thread.is_alive():
                self._stop.clear()
                self._wakeup.set()
                return
            self._stop.clear()
            self._wakeup.clear()
            spawned = threading.Thread(
                target=self._loop,
                name=_CORE_SCHEDULER_THREAD_NAME,
                daemon=False,
            )
            self._thread = spawned
        if spawned is not None:
            spawned.start()

    def _signal_stop_unlocked(self) -> threading.Thread | None:
        with self._lifecycle_lock:
            self._stop.set()
            self._wakeup.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            return thread
        return None

    def _begin_shutdown_unlocked(self) -> threading.Thread | None:
        with self._lifecycle_lock:
            self._closing.set()
            self._stop.set()
            self._wakeup.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            return thread
        return None

    def _join_worker(self, thread: threading.Thread | None, *, timeout: float) -> None:
        if thread is not None and thread.is_alive():
            deadline = monotonic() + timeout
            while thread.is_alive() and self._stop.is_set() and not self._closing.is_set():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                thread.join(timeout=min(remaining, _CORE_SCHEDULER_JOIN_POLL_SECONDS))
        self._clear_dead_thread()

    def _clear_dead_thread(self) -> None:
        with self._lifecycle_lock:
            current = self._thread
            if current is not None and not current.is_alive():
                self._thread = None

    def _try_exit(self, current: threading.Thread) -> bool:
        with self._lifecycle_lock:
            if not self._closing.is_set() and not self._stop.is_set():
                return False
            if self._thread is current:
                self._thread = None
            return True

    def _content_free_cycle_failure(self, error_code: str) -> SchedulerRunReport:
        allowed = self.dispatch_allowed()
        if not allowed:
            self._scheduler.disable()
        return SchedulerRunReport(
            plan=SchedulePlan(enabled=allowed),
            health=HealthSnapshot("unavailable", reason_codes=(error_code,)),
        )

    def run_cycle(self) -> SchedulerRunReport:
        """Run one scheduled cycle without waiting. Fail closed per source."""

        acquired = self._cycle_lock.acquire(blocking=False)
        if not acquired:
            return SchedulerRunReport(plan=SchedulePlan(enabled=self.dispatch_allowed()))
        try:
            if not self.dispatch_allowed():
                self._scheduler.disable()
                return SchedulerRunReport(plan=SchedulePlan(enabled=False))
            refresh_local_workspace_adapter(self.coordinator, self.config)
            self._scheduler.enable()
            return self._scheduler.run_once()
        except CaptureError as error:
            return self._content_free_cycle_failure(error.code)
        except OSError:
            return self._content_free_cycle_failure("capture_failed")
        finally:
            self._cycle_lock.release()

    def _loop(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                if self._try_exit(current):
                    return
                try:
                    if self.dispatch_allowed():
                        self.run_cycle()
                except (CaptureError, OSError):
                    pass
                if self._try_exit(current):
                    return
                self._wakeup.wait(timeout=float(self._scheduler.config.poll_interval_seconds))
                self._wakeup.clear()
        finally:
            with self._lifecycle_lock:
                if self._thread is current:
                    self._thread = None


__all__ = [
    "CAPTURE_SCHEDULER_ENABLED_ENV",
    "UPDATE_HEALTH_OPERATION_ENV",
    "CaptureScheduler",
    "ConnectorHealth",
    "ConnectorLimits",
    "ConnectorResourceBounds",
    "CoreCaptureScheduler",
    "HealthAction",
    "HealthSnapshot",
    "ReauthorizationState",
    "ScheduleEntry",
    "SchedulePlan",
    "SchedulerConfig",
    "SchedulerRunReport",
    "capture_scheduler_status_payload",
    "scheduler_process_gate_open",
    "scheduler_update_health_forced_off",
]
