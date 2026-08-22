"""Disabled-by-default scheduling around the Core capture coordinator.

This module is intentionally an orchestration seam, not another capture
authority.  The coordinator remains responsible for leases, lifecycle
transitions, replay/idempotency, checkpoints, and persisted retry metadata.
The scheduler only reads those projections, chooses due work, and invokes the
coordinator.  It keeps no cursor, event ledger, lease, or durable notification
state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import sleep
from typing import Literal

from .capture import (
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureError,
    CaptureRunResult,
    CaptureSource,
)

type SchedulerClock = Callable[[], str]
type SchedulerSleeper = Callable[[float], None]
type CaptureRunner = Callable[[str], CaptureRunResult]
type ResourceCost = Callable[[CaptureSource], int]
ScheduleKind = Literal["initial_backfill", "incremental", "retry"]
HealthState = Literal["healthy", "degraded", "unavailable"]


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
    max_workers: int = 4
    default_connector_limits: ConnectorLimits = field(default_factory=ConnectorLimits)

    def __post_init__(self) -> None:
        if (
            type(self.enabled) is not bool
            or type(self.poll_interval_seconds) is not int
            or type(self.incremental_interval_seconds) is not int
            or type(self.max_sources_per_cycle) is not int
            or type(self.max_workers) is not int
            or self.poll_interval_seconds < 1
            or self.incremental_interval_seconds < 1
            or not 1 <= self.max_sources_per_cycle <= 500
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

    def _sources(self) -> tuple[CaptureSource, ...]:
        sources, _total = self.coordinator.list_sources(
            limit=self.config.max_sources_per_cycle,
        )
        return tuple(sources)

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
        for source in self._sources():
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
                assert manifest is not None
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
        return HealthSnapshot(
            state=overall,
            connectors=tuple(connectors),
            reauthorization_required=tuple(reauthorization),
            actions=tuple(new_actions),
        )

    def health(self) -> HealthSnapshot:
        """Aggregate content-free health and deduplicate reauthorization action."""

        return self._health_from_sources(self._sources())

    def run_once(self) -> SchedulerRunReport:
        """Dispatch one bounded cycle; no real-time waiting occurs here."""

        plan = self.plan()
        if not plan.enabled:
            return SchedulerRunReport(
                plan=plan,
                health=self._health_from_sources(self._sources(), emit_actions=False),
            )
        selected, deferred = self._select(plan.entries)
        results: list[CaptureRunResult] = []
        if selected:
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


__all__ = [
    "CaptureScheduler",
    "ConnectorHealth",
    "ConnectorLimits",
    "ConnectorResourceBounds",
    "HealthAction",
    "HealthSnapshot",
    "ReauthorizationState",
    "ScheduleEntry",
    "SchedulePlan",
    "SchedulerConfig",
    "SchedulerRunReport",
]
