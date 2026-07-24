"""Bounded read-only programmatic inspection over a lossless structured event log.

This research adapter is intentionally small and deterministic.  It supports a
closed set of schema-driven inspection strategies; it does not execute supplied
code, interpret imported text as instructions, contact Core, or mutate
canonical state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from allthecontext.memory_lab import AdapterManifest, MemoryObject, RankedMemory

PROGRAMMATIC_LOG_SCHEMA = "atc.memory-lab.structured-event.v1"
_FIELD_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_SUPPORTED_STRATEGIES = frozenset({"latest_route", "threshold_route"})


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class StructuredLogEvent:
    """One synthetic event whose complete structure remains available to readers."""

    event_id: str
    sequence: int
    occurred_at: str
    event_type: str
    payload: tuple[tuple[str, str], ...]
    scopes: tuple[str, ...] = ()
    supersedes: str | None = None
    expires_at: str | None = None
    schema: str = PROGRAMMATIC_LOG_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROGRAMMATIC_LOG_SCHEMA:
            raise ValueError(f"unsupported structured event schema: {self.schema}")
        if not self.event_id.strip() or not self.event_type.strip():
            raise ValueError("event id and type must be non-blank")
        if self.sequence < 0:
            raise ValueError("event sequence must be non-negative")
        occurred_at = _instant(self.occurred_at)
        if self.expires_at is not None and _instant(self.expires_at) <= occurred_at:
            raise ValueError("expires_at must be later than occurred_at")
        keys = [key for key, _ in self.payload]
        if len(keys) != len(set(keys)):
            raise ValueError("structured event payload keys must be unique")
        if any(not _FIELD_RE.fullmatch(key) or not value.strip() for key, value in self.payload):
            raise ValueError("payload fields and values must be non-blank normalized strings")

    @property
    def fields(self) -> dict[str, str]:
        """Return a fresh field mapping so callers cannot mutate the event."""

        return dict(self.payload)

    def content_document(self) -> str:
        """Render event content; identity, scope, and lifecycle remain separate metadata."""

        value = {
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "payload": self.fields,
            "schema": self.schema,
            "sequence": self.sequence,
        }
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def as_memory_object(self) -> MemoryObject:
        """Preserve content and structural metadata without adding oracle data."""

        return MemoryObject(
            object_id=self.event_id,
            kind=self.event_type,
            content=self.content_document(),
            scopes=self.scopes,
            tags=tuple(f"{key}:{value}" for key, value in self.payload),
            valid_from=self.occurred_at,
            expires_at=self.expires_at,
            supersedes=self.supersedes,
        )


@dataclass(frozen=True, slots=True)
class InspectionDescriptor:
    """Adapter-visible task structure; no answer or evidence labels are present."""

    strategy: str
    selectors: tuple[tuple[str, str], ...]
    observation_event_type: str
    policy_event_type: str
    stage: str
    outcome_field: str = "outcome"
    policy_match_field: str = "when_outcome"
    action_field: str = "action"
    trigger_value: str | None = None
    window: int | None = None
    threshold_field: str = "min_count"

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.selectors]
        if len(keys) != len(set(keys)):
            raise ValueError("inspection selector keys must be unique")
        named_fields = (
            self.outcome_field,
            self.policy_match_field,
            self.action_field,
            self.threshold_field,
        )
        if any(not _FIELD_RE.fullmatch(value) for value in (*keys, *named_fields)):
            raise ValueError("inspection field names must be normalized")
        if any(not value.strip() for _, value in self.selectors):
            raise ValueError("inspection selector values must be non-blank")
        if not all(
            value.strip()
            for value in (
                self.strategy,
                self.observation_event_type,
                self.policy_event_type,
                self.stage,
            )
        ):
            raise ValueError("inspection descriptor values must be non-blank")
        if self.strategy == "threshold_route":
            if self.trigger_value is None or self.window is None or self.window < 1:
                raise ValueError("threshold_route requires a trigger value and positive window")
        elif self.trigger_value is not None or self.window is not None:
            raise ValueError("only threshold_route accepts trigger and window values")

    def lexical_query(self) -> str:
        """Provide the same descriptor values to text-only comparison conditions."""

        values = [
            self.strategy,
            *(value for _, value in self.selectors),
            self.observation_event_type,
            self.policy_event_type,
            self.stage,
        ]
        if self.trigger_value is not None:
            values.append(self.trigger_value)
        return " ".join(values)


@dataclass(frozen=True, slots=True)
class InspectionTask:
    """One adapter-visible task under a frozen clock and fixed budgets."""

    task_id: str
    descriptor: InspectionDescriptor
    evaluated_at: str
    scopes: tuple[str, ...]
    current_project: str | None
    limit: int
    context_budget_chars: int

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task id must be non-blank")
        if self.limit < 1 or self.context_budget_chars < 1:
            raise ValueError("task limits must be positive")
        _instant(self.evaluated_at)


@dataclass(frozen=True, slots=True)
class ProgrammaticInspectionLimits:
    """Hard limits enforced independently for every inspection invocation."""

    max_operations: int
    max_events_scanned_per_operation: int
    max_results_per_operation: int

    def __post_init__(self) -> None:
        if (
            min(
                self.max_operations,
                self.max_events_scanned_per_operation,
                self.max_results_per_operation,
            )
            < 1
        ):
            raise ValueError("programmatic inspection limits must be positive")


@dataclass(frozen=True, slots=True)
class InspectionOperation:
    """Identifier-free receipt for one bounded read-only operation."""

    operation: str
    scanned_count: int
    result_count: int


@dataclass(frozen=True, slots=True)
class InspectionReceipt:
    """Selected event identifiers plus bounded-operation accounting."""

    items: tuple[RankedMemory, ...]
    abstained: bool
    operations: tuple[InspectionOperation, ...]
    reason_code: str

    def __post_init__(self) -> None:
        if self.abstained == bool(self.items):
            raise ValueError("abstained must be true exactly when no items are returned")


class _ProgramRun:
    def __init__(self, limits: ProgrammaticInspectionLimits) -> None:
        self.limits = limits
        self.operations: list[InspectionOperation] = []

    def preflight(self, *, scanned: int) -> bool:
        """Reject an operation before it scans or materializes beyond its cap."""

        return (
            len(self.operations) < self.limits.max_operations
            and scanned <= self.limits.max_events_scanned_per_operation
        )

    def result_slot_available(self, materialized_results: int) -> bool:
        """Return whether one more result may be materialized."""

        return materialized_results < self.limits.max_results_per_operation

    def record(self, operation: str, *, scanned: int, results: int) -> None:
        if not self.preflight(scanned=scanned) or results > self.limits.max_results_per_operation:
            raise RuntimeError("operation receipt exceeded its preflighted bounds")
        self.operations.append(
            InspectionOperation(
                operation=operation,
                scanned_count=scanned,
                result_count=results,
            )
        )


def _bounded_filter(
    values: Sequence[StructuredLogEvent],
    run: _ProgramRun,
    predicate: Callable[[StructuredLogEvent], bool],
) -> tuple[StructuredLogEvent, ...] | None:
    """Filter only after preflight and stop before materializing an excess result."""

    if not run.preflight(scanned=len(values)):
        return None
    selected: list[StructuredLogEvent] = []
    for value in values:
        if predicate(value):
            if not run.result_slot_available(len(selected)):
                return None
            selected.append(value)
    return tuple(selected)


class ProgrammaticLogInspectionAdapter:
    """Restricted multi-operation reader for latest-route and threshold-route tasks."""

    manifest = AdapterManifest(
        adapter_id="bounded-programmatic-structured-log",
        name="Bounded native programmatic structured-log inspection",
        version="b01-v1",
    )

    def __init__(self, limits: ProgrammaticInspectionLimits) -> None:
        self._limits = limits
        self._events: tuple[StructuredLogEvent, ...] = ()

    @property
    def supported_strategies(self) -> frozenset[str]:
        return _SUPPORTED_STRATEGIES

    def prepare(self, events: Sequence[StructuredLogEvent]) -> int:
        if self._events:
            raise RuntimeError("programmatic log adapter may only be prepared once")
        event_ids = [event.event_id for event in events]
        sequences = [event.sequence for event in events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("structured event ids must be unique")
        if len(sequences) != len(set(sequences)):
            raise ValueError("structured event sequences must be unique")
        self._events = tuple(events)
        return sum(len(event.content_document().encode("utf-8")) for event in events)

    def inspect(self, task: InspectionTask) -> InspectionReceipt:
        if not self._events:
            raise RuntimeError("programmatic log adapter must be prepared before inspection")
        if task.descriptor.strategy not in self.supported_strategies:
            return InspectionReceipt(
                items=(),
                abstained=True,
                operations=(),
                reason_code="unsupported_strategy",
            )

        run = _ProgramRun(self._limits)
        current = self._resolve_current(task, run)
        if current is None:
            return self._failed(run, "operation_budget_exhausted")
        if task.descriptor.strategy == "latest_route":
            selected = self._latest_route(task, current, run)
        else:
            selected = self._threshold_route(task, current, run)
        if selected is None:
            return self._failed(run, "operation_budget_exhausted")
        if not selected:
            return self._failed(run, "no_matching_route")
        items = self._budgeted_items(selected, task)
        return InspectionReceipt(
            items=items,
            abstained=not items,
            operations=tuple(run.operations),
            reason_code="selected" if items else "context_budget_exhausted",
        )

    def close(self) -> None:
        self._events = ()

    def _resolve_current(
        self,
        task: InspectionTask,
        run: _ProgramRun,
    ) -> tuple[StructuredLogEvent, ...] | None:
        maximum_scan_work = 2 * len(self._events)
        if not run.preflight(scanned=maximum_scan_work):
            return None
        effective_at = _instant(task.evaluated_at)
        historical_values: list[StructuredLogEvent] = []
        for event in self._events:
            if _instant(event.occurred_at) <= effective_at:
                if not run.result_slot_available(len(historical_values)):
                    return None
                historical_values.append(event)
        historical = tuple(historical_values)
        superseded = {event.supersedes for event in historical if event.supersedes is not None}
        current_values: list[StructuredLogEvent] = []
        for event in historical:
            if (
                event.event_id not in superseded
                and (event.expires_at is None or _instant(event.expires_at) > effective_at)
                and (not task.scopes or set(task.scopes).intersection(event.scopes))
                and self._project_applies(event, task.current_project)
            ):
                if not run.result_slot_available(len(current_values)):
                    return None
                current_values.append(event)
        current = tuple(current_values)
        run.record(
            "resolve_current_authorized_snapshot",
            scanned=len(self._events) + len(historical),
            results=len(current),
        )
        return current

    def _latest_route(
        self,
        task: InspectionTask,
        current: tuple[StructuredLogEvent, ...],
        run: _ProgramRun,
    ) -> tuple[StructuredLogEvent, ...] | None:
        descriptor = task.descriptor
        observations = _bounded_filter(
            current,
            run,
            lambda event: self._matches(
                event,
                descriptor.selectors,
                event_type=descriptor.observation_event_type,
                stage=descriptor.stage,
            ),
        )
        if observations is None:
            return None
        run.record(
            "filter_observations",
            scanned=len(current),
            results=len(observations),
        )
        if not run.preflight(scanned=len(observations)):
            return None
        latest = tuple(sorted(observations, key=lambda event: event.sequence, reverse=True)[:1])
        run.record(
            "take_latest_by_sequence",
            scanned=len(observations),
            results=len(latest),
        )
        if not latest:
            return ()
        outcome = latest[0].fields.get(descriptor.outcome_field)
        if outcome is None:
            return ()
        if not run.preflight(scanned=len(current)):
            return None
        policy: StructuredLogEvent | None = None
        for event in current:
            if (
                self._matches(
                    event,
                    descriptor.selectors,
                    event_type=descriptor.policy_event_type,
                    stage=descriptor.stage,
                )
                and event.fields.get(descriptor.policy_match_field) == outcome
                and descriptor.action_field in event.fields
                and (policy is None or event.sequence > policy.sequence)
            ):
                policy = event
        run.record(
            "join_policy_on_latest_outcome",
            scanned=len(current),
            results=int(policy is not None),
        )
        return () if policy is None else (*latest, policy)

    def _threshold_route(
        self,
        task: InspectionTask,
        current: tuple[StructuredLogEvent, ...],
        run: _ProgramRun,
    ) -> tuple[StructuredLogEvent, ...] | None:
        descriptor = task.descriptor
        observations = _bounded_filter(
            current,
            run,
            lambda event: self._matches(
                event,
                descriptor.selectors,
                event_type=descriptor.observation_event_type,
                stage=descriptor.stage,
            ),
        )
        if observations is None:
            return None
        run.record(
            "filter_observations",
            scanned=len(current),
            results=len(observations),
        )
        if not run.preflight(scanned=len(observations)):
            return None
        ordered = tuple(sorted(observations, key=lambda event: event.sequence, reverse=True))
        window = ordered[: descriptor.window]
        if len(window) > run.limits.max_results_per_operation:
            return None
        run.record(
            "take_recent_window",
            scanned=len(observations),
            results=len(window),
        )
        if not run.preflight(scanned=len(window)):
            return None
        matching = tuple(
            event
            for event in window
            if event.fields.get(descriptor.outcome_field) == descriptor.trigger_value
        )
        run.record(
            "aggregate_trigger_count",
            scanned=len(window),
            results=1,
        )
        if not run.preflight(scanned=len(current)):
            return None
        selected_policy: tuple[int, StructuredLogEvent] | None = None
        for event in current:
            if not self._matches(
                event,
                descriptor.selectors,
                event_type=descriptor.policy_event_type,
                stage=descriptor.stage,
            ):
                continue
            fields = event.fields
            if (
                fields.get(descriptor.policy_match_field) != descriptor.trigger_value
                or descriptor.action_field not in fields
            ):
                continue
            try:
                threshold = int(fields[descriptor.threshold_field])
            except (KeyError, ValueError):
                continue
            if threshold <= len(matching):
                candidate = (threshold, event)
                if selected_policy is None or (
                    threshold,
                    event.sequence,
                ) > (
                    selected_policy[0],
                    selected_policy[1].sequence,
                ):
                    selected_policy = candidate
        run.record(
            "filter_threshold_policies",
            scanned=len(current),
            results=int(selected_policy is not None),
        )
        if selected_policy is None:
            return ()
        _, policy = selected_policy
        threshold = int(policy.fields[descriptor.threshold_field])
        return (*matching[:threshold], policy)

    @staticmethod
    def _matches(
        event: StructuredLogEvent,
        selectors: tuple[tuple[str, str], ...],
        *,
        event_type: str,
        stage: str,
    ) -> bool:
        fields = event.fields
        return (
            event.event_type == event_type
            and fields.get("stage") == stage
            and all(fields.get(key) == value for key, value in selectors)
        )

    @staticmethod
    def _project_applies(
        event: StructuredLogEvent,
        current_project: str | None,
    ) -> bool:
        if current_project is None:
            return True
        project_scopes = tuple(
            scope.removeprefix("project:") for scope in event.scopes if scope.startswith("project:")
        )
        return not project_scopes or current_project.casefold() in {
            project.casefold() for project in project_scopes
        }

    @staticmethod
    def _budgeted_items(
        events: Sequence[StructuredLogEvent],
        task: InspectionTask,
    ) -> tuple[RankedMemory, ...]:
        selected: list[RankedMemory] = []
        disclosed = 0
        for event in events:
            event_chars = len(event.content_document())
            if disclosed + event_chars > task.context_budget_chars:
                continue
            selected.append(RankedMemory(event.event_id))
            disclosed += event_chars
            if len(selected) == task.limit:
                break
        return tuple(selected)

    @staticmethod
    def _failed(run: _ProgramRun, reason_code: str) -> InspectionReceipt:
        return InspectionReceipt(
            items=(),
            abstained=True,
            operations=tuple(run.operations),
            reason_code=reason_code,
        )
