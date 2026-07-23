"""Run Wave 3 B01: bounded programmatic inspection of a structured event log.

The experiment is local, deterministic, sanitized, and read-only.  Gold action
and evidence labels remain in the harness and are never included in an
adapter-facing task.  No external system, provider, network service, personal
context, operator Core, or model is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from allthecontext.memory_lab import (
    AdapterManifest,
    MemoryLabAdapter,
    NoMemoryBaseline,
    PreparationReceipt,
    RetrievalTask,
)
from allthecontext.memory_lab_baselines import StableObservationLogBaseline
from allthecontext.memory_lab_programmatic_log import (
    InspectionDescriptor,
    InspectionOperation,
    InspectionReceipt,
    InspectionTask,
    ProgrammaticInspectionLimits,
    ProgrammaticLogInspectionAdapter,
    StructuredLogEvent,
)

from bench.memory_lab import AtcRetrievalAdapter

FIXTURES = Path(__file__).with_name("memory_lab_b01_fixtures.json")
CONFIG = Path(__file__).with_name("memory_lab_b01_config.json")
REPORT_SCHEMA = "atc.memory-lab.b01-report.v1"
CONDITION_ORDER = (
    "no-memory",
    "stable-observation-current-state",
    "bounded-programmatic-structured-log",
    "atc-retrieval-v3",
    "frozen-programmatic-atc-combination",
)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


@dataclass(frozen=True, slots=True)
class B01Config:
    repeats: int
    frozen_clock: str
    context_budget_chars: int
    max_selected_events: int
    max_operations: int
    max_events_scanned_per_operation: int
    max_results_per_operation: int
    confirmatory_partition: str
    minimum_confirmatory_caos_gain: float
    maximum_operation_premium: float
    combination_recipe: str

    def __post_init__(self) -> None:
        if min(
            self.repeats,
            self.context_budget_chars,
            self.max_selected_events,
            self.max_operations,
            self.max_events_scanned_per_operation,
            self.max_results_per_operation,
        ) < 1:
            raise ValueError("B01 limits and repeats must be positive")
        if not 0.0 <= self.minimum_confirmatory_caos_gain <= 1.0:
            raise ValueError("minimum CAOS gain must be between zero and one")
        if self.maximum_operation_premium < 0.0:
            raise ValueError("maximum operation premium must be non-negative")
        if (
            self.combination_recipe
            != "programmatic_then_atc_only_for_unsupported_strategy"
        ):
            raise ValueError("unsupported frozen B01 combination recipe")

    @property
    def programmatic_limits(self) -> ProgrammaticInspectionLimits:
        return ProgrammaticInspectionLimits(
            max_operations=self.max_operations,
            max_events_scanned_per_operation=self.max_events_scanned_per_operation,
            max_results_per_operation=self.max_results_per_operation,
        )


@dataclass(frozen=True, slots=True)
class TaskOracle:
    expected_action: str | None
    evidence_groups: tuple[frozenset[str], ...]
    forbidden_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class B01Scenario:
    partition: str
    task: InspectionTask
    oracle: TaskOracle


class B01Condition(Protocol):
    @property
    def manifest(self) -> AdapterManifest: ...

    def prepare(self, events: Sequence[StructuredLogEvent]) -> PreparationReceipt: ...

    def inspect(self, task: InspectionTask) -> InspectionReceipt: ...

    def close(self) -> None: ...


class RetrievalCondition:
    """Expose an existing one-shot retrieval adapter through the B01 condition seam."""

    def __init__(
        self,
        adapter: MemoryLabAdapter,
        *,
        operation_name: str,
        count_operation: bool = True,
    ) -> None:
        self._adapter = adapter
        self._operation_name = operation_name
        self._count_operation = count_operation

    @property
    def manifest(self) -> AdapterManifest:
        return self._adapter.manifest

    def prepare(self, events: Sequence[StructuredLogEvent]) -> PreparationReceipt:
        return self._adapter.prepare(tuple(event.as_memory_object() for event in events))

    def inspect(self, task: InspectionTask) -> InspectionReceipt:
        receipt = self._adapter.retrieve(_retrieval_task(task))
        operations = (
            (
                InspectionOperation(
                    operation=self._operation_name,
                    scanned_count=0,
                    result_count=len(receipt.items),
                ),
            )
            if self._count_operation
            else ()
        )
        return InspectionReceipt(
            items=receipt.items,
            abstained=receipt.abstained,
            operations=operations,
            reason_code="selected" if receipt.items else "no_match",
        )

    def close(self) -> None:
        self._adapter.close()


class NativeProgrammaticCondition:
    def __init__(self, limits: ProgrammaticInspectionLimits) -> None:
        self._adapter = ProgrammaticLogInspectionAdapter(limits)

    @property
    def manifest(self) -> AdapterManifest:
        return self._adapter.manifest

    def prepare(self, events: Sequence[StructuredLogEvent]) -> PreparationReceipt:
        return PreparationReceipt(storage_bytes=self._adapter.prepare(events))

    def inspect(self, task: InspectionTask) -> InspectionReceipt:
        return self._adapter.inspect(task)

    def close(self) -> None:
        self._adapter.close()


class FrozenProgrammaticAtcCombination:
    """Frozen fallback: use ATC only when the hand-authored DSL is unsupported."""

    manifest = AdapterManifest(
        adapter_id="frozen-programmatic-atc-combination",
        name="Frozen programmatic log plus ATC unsupported-strategy fallback",
        version="b01-v1",
    )

    def __init__(
        self,
        work_dir: Path,
        config: B01Config,
    ) -> None:
        self._programmatic = ProgrammaticLogInspectionAdapter(config.programmatic_limits)
        self._atc = AtcRetrievalAdapter(
            work_dir,
            context_budget_chars=config.context_budget_chars,
        )
        self._max_operations = config.max_operations

    def prepare(self, events: Sequence[StructuredLogEvent]) -> PreparationReceipt:
        programmatic_storage = self._programmatic.prepare(events)
        atc_preparation = self._atc.prepare(
            tuple(event.as_memory_object() for event in events)
        )
        return PreparationReceipt(
            storage_bytes=programmatic_storage + atc_preparation.storage_bytes
        )

    def inspect(self, task: InspectionTask) -> InspectionReceipt:
        programmatic = self._programmatic.inspect(task)
        if programmatic.reason_code != "unsupported_strategy":
            return programmatic
        atc = self._atc.retrieve(_retrieval_task(task))
        operations = (
            *programmatic.operations,
            InspectionOperation(
                operation="atc_unsupported_strategy_fallback",
                scanned_count=0,
                result_count=len(atc.items),
            ),
        )
        if len(operations) > self._max_operations:
            return InspectionReceipt(
                items=(),
                abstained=True,
                operations=programmatic.operations,
                reason_code="operation_budget_exhausted",
            )
        return InspectionReceipt(
            items=atc.items,
            abstained=atc.abstained,
            operations=operations,
            reason_code="atc_fallback_selected" if atc.items else "atc_fallback_no_match",
        )

    def close(self) -> None:
        self._programmatic.close()
        self._atc.close()


def _retrieval_task(task: InspectionTask) -> RetrievalTask:
    """Translate only the adapter-visible descriptor; oracle fields remain absent."""

    return RetrievalTask(
        task_id=task.task_id,
        query=task.descriptor.lexical_query(),
        evaluated_at=task.evaluated_at,
        limit=task.limit,
        evidence_groups=(),
        forbidden_ids=frozenset(),
        scopes=task.scopes,
        current_project=task.current_project,
        context_budget_chars=task.context_budget_chars,
    )


def _load_config(path: Path = CONFIG) -> B01Config:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != "atc.memory-lab.b01-config.v1":
        raise ValueError("unsupported B01 config schema")
    combination = raw.get("frozen_combination")
    if (
        not isinstance(combination, dict)
        or combination.get("schema") != "atc.memory-lab.b01-combination.v1"
        or combination.get("programmatic_precedence") is not True
        or combination.get("deduplicate_by_event_id") is not True
    ):
        raise ValueError("invalid frozen B01 combination")
    return B01Config(
        repeats=int(raw["repeats"]),
        frozen_clock=str(raw["frozen_clock"]),
        context_budget_chars=int(raw["context_budget_chars"]),
        max_selected_events=int(raw["max_selected_events"]),
        max_operations=int(raw["max_operations"]),
        max_events_scanned_per_operation=int(
            raw["max_events_scanned_per_operation"]
        ),
        max_results_per_operation=int(raw["max_results_per_operation"]),
        confirmatory_partition=str(raw["confirmatory_partition"]),
        minimum_confirmatory_caos_gain=float(raw["minimum_confirmatory_caos_gain"]),
        maximum_operation_premium=float(raw["maximum_operation_premium"]),
        combination_recipe=str(combination["recipe"]),
    )


def _load_descriptor(raw: Any) -> InspectionDescriptor:
    if not isinstance(raw, dict):
        raise ValueError("B01 descriptor must be an object")
    selectors = raw.get("selectors")
    if not isinstance(selectors, dict):
        raise ValueError("B01 descriptor selectors must be an object")
    return InspectionDescriptor(
        strategy=str(raw["strategy"]),
        selectors=tuple((str(key), str(value)) for key, value in selectors.items()),
        observation_event_type=str(raw["observation_event_type"]),
        policy_event_type=str(raw["policy_event_type"]),
        stage=str(raw["stage"]),
        outcome_field=str(raw.get("outcome_field", "outcome")),
        policy_match_field=str(raw.get("policy_match_field", "when_outcome")),
        action_field=str(raw.get("action_field", "action")),
        trigger_value=(
            str(raw["trigger_value"]) if raw.get("trigger_value") is not None else None
        ),
        window=int(raw["window"]) if raw.get("window") is not None else None,
        threshold_field=str(raw.get("threshold_field", "min_count")),
    )


def load_fixture_bundle(
    fixture_path: Path = FIXTURES,
    config_path: Path = CONFIG,
) -> tuple[tuple[StructuredLogEvent, ...], tuple[B01Scenario, ...], B01Config]:
    """Load and validate the frozen sanitized B01 corpus and hidden oracles."""

    config = _load_config(config_path)
    raw: Any = json.loads(fixture_path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != "atc.memory-lab.b01-fixture.v1"
        or raw.get("corpus_kind") != "sanitized_symbolic_structured_event_log"
    ):
        raise ValueError("unsupported or unsanitized B01 fixture")
    raw_events = raw.get("events")
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_events, list) or not isinstance(raw_tasks, list):
        raise ValueError("B01 events and tasks must be lists")
    events: list[StructuredLogEvent] = []
    for value in raw_events:
        if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
            raise ValueError("B01 events and payloads must be objects")
        payload = value["payload"]
        events.append(
            StructuredLogEvent(
                event_id=str(value["event_id"]),
                sequence=int(value["sequence"]),
                occurred_at=str(value["occurred_at"]),
                event_type=str(value["event_type"]),
                payload=tuple(
                    (str(key), str(payload_value))
                    for key, payload_value in payload.items()
                ),
                scopes=tuple(str(item) for item in value.get("scopes", ())),
                supersedes=(
                    str(value["supersedes"])
                    if value.get("supersedes") is not None
                    else None
                ),
                expires_at=(
                    str(value["expires_at"])
                    if value.get("expires_at") is not None
                    else None
                ),
            )
        )
    event_ids = {event.event_id for event in events}
    if len(event_ids) != len(events):
        raise ValueError("B01 event ids must be unique")
    if 2 * len(events) > config.max_events_scanned_per_operation:
        raise ValueError("B01 current-state resolution exceeds the frozen scan cap")

    scenarios: list[B01Scenario] = []
    for value in raw_tasks:
        if not isinstance(value, dict) or not isinstance(value.get("oracle"), dict):
            raise ValueError("B01 tasks and oracles must be objects")
        oracle_raw = value["oracle"]
        raw_groups = oracle_raw.get("evidence_groups")
        if not isinstance(raw_groups, list):
            raise ValueError("B01 evidence groups must be lists")
        task = InspectionTask(
            task_id=str(value["task_id"]),
            descriptor=_load_descriptor(value["descriptor"]),
            evaluated_at=str(value["evaluated_at"]),
            scopes=tuple(str(item) for item in value.get("scopes", ())),
            current_project=(
                str(value["current_project"])
                if value.get("current_project") is not None
                else None
            ),
            limit=config.max_selected_events,
            context_budget_chars=config.context_budget_chars,
        )
        if task.evaluated_at != config.frozen_clock:
            raise ValueError("every B01 task must use the same frozen logical clock")
        oracle = TaskOracle(
            expected_action=(
                str(oracle_raw["expected_action"])
                if oracle_raw.get("expected_action") is not None
                else None
            ),
            evidence_groups=tuple(
                frozenset(str(event_id) for event_id in group) for group in raw_groups
            ),
            forbidden_ids=frozenset(
                str(item) for item in oracle_raw.get("forbidden_ids", ())
            ),
        )
        referenced = frozenset().union(*oracle.evidence_groups) | oracle.forbidden_ids
        if not referenced <= event_ids:
            raise ValueError("B01 oracle references events outside the fixture")
        scenarios.append(
            B01Scenario(
                partition=str(value["partition"]),
                task=task,
                oracle=oracle,
            )
        )
    if not any(item.partition == config.confirmatory_partition for item in scenarios):
        raise ValueError("B01 fixture has no confirmatory scenarios")
    return tuple(events), tuple(scenarios), config


def _matches(event: StructuredLogEvent, descriptor: InspectionDescriptor) -> bool:
    fields = event.fields
    return (
        fields.get("stage") == descriptor.stage
        and all(fields.get(key) == value for key, value in descriptor.selectors)
    )


def _execute_selected_context(
    events_by_id: dict[str, StructuredLogEvent],
    task: InspectionTask,
    selected_ids: Sequence[str],
) -> str | None:
    """Common frozen action executor over selected context only, without oracle access."""

    selected = tuple(
        events_by_id[event_id] for event_id in selected_ids if event_id in events_by_id
    )
    descriptor = task.descriptor
    if descriptor.strategy == "latest_route":
        observations = tuple(
            event
            for event in selected
            if event.event_type == descriptor.observation_event_type
            and _matches(event, descriptor)
        )
        latest = max(observations, key=lambda event: event.sequence, default=None)
        if latest is None:
            return None
        outcome = latest.fields.get(descriptor.outcome_field)
        policies = tuple(
            event
            for event in selected
            if event.event_type == descriptor.policy_event_type
            and _matches(event, descriptor)
            and event.fields.get(descriptor.policy_match_field) == outcome
        )
        policy = max(policies, key=lambda event: event.sequence, default=None)
        return None if policy is None else policy.fields.get(descriptor.action_field)
    if descriptor.strategy == "threshold_route":
        threshold_observations = sorted(
            (
                event
                for event in selected
                if event.event_type == descriptor.observation_event_type
                and _matches(event, descriptor)
            ),
            key=lambda event: event.sequence,
            reverse=True,
        )
        window = threshold_observations[: descriptor.window]
        count = sum(
            event.fields.get(descriptor.outcome_field) == descriptor.trigger_value
            for event in window
        )
        eligible: list[tuple[int, StructuredLogEvent]] = []
        for event in selected:
            if (
                event.event_type != descriptor.policy_event_type
                or not _matches(event, descriptor)
                or event.fields.get(descriptor.policy_match_field)
                != descriptor.trigger_value
            ):
                continue
            try:
                threshold = int(event.fields[descriptor.threshold_field])
            except (KeyError, ValueError):
                continue
            if threshold <= count:
                eligible.append((threshold, event))
        if not eligible:
            return None
        _, policy = max(eligible, key=lambda item: (item[0], item[1].sequence))
        return policy.fields.get(descriptor.action_field)
    if descriptor.strategy == "lexical_lookup":
        facts = tuple(
            event
            for event in selected
            if event.event_type == descriptor.observation_event_type
            and _matches(event, descriptor)
        )
        fact = max(facts, key=lambda event: event.sequence, default=None)
        return None if fact is None else fact.fields.get(descriptor.action_field)
    return None


def _task_result(
    *,
    task_index: int,
    scenario: B01Scenario,
    events_by_id: dict[str, StructuredLogEvent],
    receipts: Sequence[InspectionReceipt],
    actions: Sequence[str | None],
    latencies_ms: Sequence[float],
    config: B01Config,
) -> dict[str, Any]:
    first = receipts[0]
    ids = tuple(item.object_id for item in first.items)
    unique_ids = set(ids)
    unknown_ids = unique_ids - events_by_id.keys()
    duplicate_count = len(ids) - len(unique_ids)
    over_limit_count = max(0, len(ids) - scenario.task.limit)
    contract_violations = len(unknown_ids) + duplicate_count + over_limit_count
    known_ids = unique_ids & events_by_id.keys()
    disclosure_chars = sum(
        len(events_by_id[event_id].content_document()) for event_id in known_ids
    )
    context_budget_violations = int(
        disclosure_chars > scenario.task.context_budget_chars
    )
    operation_budget_violations = sum(
        len(receipt.operations) > config.max_operations for receipt in receipts
    )
    forbidden_count = len(known_ids & scenario.oracle.forbidden_ids)
    group_hits = sum(
        bool(known_ids & group) for group in scenario.oracle.evidence_groups
    )
    group_recall = (
        group_hits / len(scenario.oracle.evidence_groups)
        if scenario.oracle.evidence_groups
        else 1.0
    )
    relevant_ids = frozenset().union(*scenario.oracle.evidence_groups)
    precision = len(known_ids & relevant_ids) / len(known_ids) if known_ids else 0.0
    action_correct = actions[0] == scenario.oracle.expected_action
    abstention_correct = (
        first.abstained if scenario.oracle.expected_action is None else None
    )
    deterministic = all(
        receipt.items == first.items
        and receipt.operations == first.operations
        and receipt.reason_code == first.reason_code
        and action == actions[0]
        for receipt, action in zip(receipts[1:], actions[1:], strict=True)
    )
    caos = (
        action_correct
        and (abstention_correct is not False)
        and group_recall == 1.0
        and forbidden_count == 0
        and contract_violations == 0
        and context_budget_violations == 0
        and operation_budget_violations == 0
        and deterministic
    )
    failure_reason_codes: list[str] = []
    if not action_correct:
        failure_reason_codes.append("incorrect_action")
    if abstention_correct is False:
        failure_reason_codes.append("abstention_mismatch")
    if group_recall < 1.0:
        failure_reason_codes.append("required_evidence_missing")
    if forbidden_count:
        failure_reason_codes.append("forbidden_output")
    if contract_violations:
        failure_reason_codes.append("adapter_contract_violation")
    if context_budget_violations:
        failure_reason_codes.append("context_budget_exceeded")
    if operation_budget_violations:
        failure_reason_codes.append("operation_budget_exceeded")
    if not deterministic:
        failure_reason_codes.append("nondeterministic_result")
    ordinal = {
        event_id: f"event-{index:06d}"
        for index, event_id in enumerate(events_by_id)
    }
    fingerprint_source = "\n".join(
        ordinal.get(event_id, "unknown-event") for event_id in ids
    )
    return {
        "task_index": task_index,
        "partition": scenario.partition,
        "strategy": scenario.task.descriptor.strategy,
        "caos": caos,
        "action_correct": action_correct,
        "abstention_correct": abstention_correct,
        "evidence_group_recall": round(group_recall, 6),
        "precision": round(precision, 6),
        "returned_count": len(ids),
        "ranking_fingerprint": hashlib.sha256(
            fingerprint_source.encode("utf-8")
        ).hexdigest(),
        "forbidden_output_count": forbidden_count,
        "contract_violation_count": contract_violations,
        "context_budget_violation_count": context_budget_violations,
        "operation_budget_violation_count": operation_budget_violations,
        "disclosure_chars": disclosure_chars,
        "operation_count": len(first.operations),
        "operation_shape": [operation.operation for operation in first.operations],
        "repeat_deterministic": deterministic,
        "failure_reason_codes": failure_reason_codes,
        "adapter_reason_code": first.reason_code,
        "latency": {
            "p50_ms": round(_percentile(latencies_ms, 0.50), 6),
            "p95_ms": round(_percentile(latencies_ms, 0.95), 6),
        },
    }


def _aggregate_tasks(tasks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task_count": len(tasks),
        "caos_rate": round(_mean([float(bool(item["caos"])) for item in tasks]), 6),
        "action_accuracy": round(
            _mean([float(bool(item["action_correct"])) for item in tasks]),
            6,
        ),
        "mean_evidence_group_recall": round(
            _mean([float(item["evidence_group_recall"]) for item in tasks]),
            6,
        ),
        "mean_precision": round(
            _mean([float(item["precision"]) for item in tasks]),
            6,
        ),
        "forbidden_output_count": sum(
            int(item["forbidden_output_count"]) for item in tasks
        ),
        "contract_violation_count": sum(
            int(item["contract_violation_count"]) for item in tasks
        ),
        "context_budget_violation_count": sum(
            int(item["context_budget_violation_count"]) for item in tasks
        ),
        "operation_budget_violation_count": sum(
            int(item["operation_budget_violation_count"]) for item in tasks
        ),
        "deterministic_task_rate": round(
            _mean([float(bool(item["repeat_deterministic"])) for item in tasks]),
            6,
        ),
        "mean_disclosure_chars": round(
            _mean([float(item["disclosure_chars"]) for item in tasks]),
            6,
        ),
        "mean_operations_per_task": round(
            _mean([float(item["operation_count"]) for item in tasks]),
            6,
        ),
    }


def _evaluate_condition(
    condition: B01Condition,
    events: Sequence[StructuredLogEvent],
    scenarios: Sequence[B01Scenario],
    config: B01Config,
) -> dict[str, Any]:
    events_by_id = {event.event_id: event for event in events}
    started = time.perf_counter()
    preparation = condition.prepare(events)
    preparation_ms = (time.perf_counter() - started) * 1_000
    task_reports: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    try:
        for task_index, scenario in enumerate(scenarios):
            receipts: list[InspectionReceipt] = []
            actions: list[str | None] = []
            latencies: list[float] = []
            for _ in range(config.repeats):
                invocation_started = time.perf_counter()
                receipt = condition.inspect(scenario.task)
                action = _execute_selected_context(
                    events_by_id,
                    scenario.task,
                    tuple(item.object_id for item in receipt.items),
                )
                latency_ms = (time.perf_counter() - invocation_started) * 1_000
                receipts.append(receipt)
                actions.append(action)
                latencies.append(latency_ms)
                all_latencies.append(latency_ms)
            task_reports.append(
                _task_result(
                    task_index=task_index,
                    scenario=scenario,
                    events_by_id=events_by_id,
                    receipts=receipts,
                    actions=actions,
                    latencies_ms=latencies,
                    config=config,
                )
            )
    finally:
        condition.close()
    partitions = {
        partition: _aggregate_tasks(
            tuple(item for item in task_reports if item["partition"] == partition)
        )
        for partition in sorted({scenario.partition for scenario in scenarios})
    }
    family_metrics = {
        strategy: _aggregate_tasks(
            tuple(
                item
                for item in task_reports
                if item["strategy"] == strategy
                and item["partition"] == config.confirmatory_partition
            )
        )
        for strategy in sorted(
            {
                scenario.task.descriptor.strategy
                for scenario in scenarios
                if scenario.partition == config.confirmatory_partition
            }
        )
    }
    return {
        "manifest": {
            "adapter_id": condition.manifest.adapter_id,
            "name": condition.manifest.name,
            "version": condition.manifest.version,
            "abi": condition.manifest.abi,
            "network_access": condition.manifest.network_access,
            "writes_canonical_state": condition.manifest.writes_canonical_state,
        },
        "preparation": {
            "latency_ms": round(preparation_ms, 6),
            "storage_bytes": preparation.storage_bytes,
        },
        "metrics": {
            **_aggregate_tasks(task_reports),
            "retrieval_and_action_latency": {
                "p50_ms": round(_percentile(all_latencies, 0.50), 6),
                "p95_ms": round(_percentile(all_latencies, 0.95), 6),
                "p99_ms": round(_percentile(all_latencies, 0.99), 6),
            },
            "model_calls": 0,
            "provider_tokens": 0,
            "monetary_cost_usd": 0.0,
        },
        "partitions": partitions,
        "confirmatory_families": family_metrics,
        "failure_cases": [
            {
                "task_index": int(item["task_index"]),
                "reason_codes": list(item["failure_reason_codes"]),
            }
            for item in task_reports
            if item["failure_reason_codes"]
        ],
        "tasks": task_reports,
    }


def _decision(report: dict[str, Any], config: B01Config) -> dict[str, Any]:
    stable = report["conditions"]["stable-observation-current-state"]["partitions"][
        config.confirmatory_partition
    ]
    programmatic = report["conditions"]["bounded-programmatic-structured-log"][
        "partitions"
    ][config.confirmatory_partition]
    caos_gain = programmatic["caos_rate"] - stable["caos_rate"]
    stable_operations = float(stable["mean_operations_per_task"])
    operation_premium = (
        (float(programmatic["mean_operations_per_task"]) - stable_operations)
        / stable_operations
        if stable_operations
        else math.inf
    )
    hard_gate_failures: list[str] = []
    for key in (
        "forbidden_output_count",
        "contract_violation_count",
        "context_budget_violation_count",
        "operation_budget_violation_count",
    ):
        if programmatic[key]:
            hard_gate_failures.append(key)
    family_gains = {
        family: round(
            metrics["caos_rate"]
            - report["conditions"]["stable-observation-current-state"][
                "confirmatory_families"
            ][family]["caos_rate"],
            6,
        )
        for family, metrics in report["conditions"][
            "bounded-programmatic-structured-log"
        ]["confirmatory_families"].items()
        if family in {"latest_route", "threshold_route"}
    }
    reason_codes: list[str] = []
    if hard_gate_failures:
        reason_codes.append("hard_gate_failure")
    if caos_gain < config.minimum_confirmatory_caos_gain:
        reason_codes.append("confirmatory_caos_gain_below_floor")
    if operation_premium > config.maximum_operation_premium:
        reason_codes.append("operation_premium_above_cap")
    if any(gain < config.minimum_confirmatory_caos_gain for gain in family_gains.values()):
        reason_codes.append("action_family_gain_below_floor")
    state = "KILL_MECHANISM" if reason_codes else "HOLD_FOR_BROADER_REPLICATION"
    combination_caos = report["conditions"]["frozen-programmatic-atc-combination"][
        "partitions"
    ][config.confirmatory_partition]["caos_rate"]
    return {
        "state": state,
        "comparator": "stable-observation-current-state",
        "confirmatory_caos_gain": round(caos_gain, 6),
        "confirmatory_operation_premium": (
            None if not math.isfinite(operation_premium) else round(operation_premium, 6)
        ),
        "action_family_caos_gains": family_gains,
        "hard_gate_failures": hard_gate_failures,
        "reason_codes": reason_codes or ["bounded_effect_clears_frozen_b01_gates"],
        "operation_accounting": (
            "counted_dsl_read_operations_vs_one_top_level_lexical_adapter_call"
        ),
        "internal_condition_work_normalized": False,
        "kill_applies_only_to": (
            "this_bounded_hand_authored_dsl_configuration_under_external_operation_accounting"
        ),
        "comparative_compute_efficiency_established": False,
        "general_programmatic_inspection_falsified": False,
        "pro_long_falsified": False,
        "combination_confirmatory_caos": combination_caos,
        "combination_disposition": (
            "NOT_PROMOTED_UNDER_SAME_B01_EXTERNAL_OPERATION_GATE"
            if state == "KILL_MECHANISM"
            else "HOLD_WITH_PROGRAMMATIC_MECHANISM"
        ),
        "scope": "isolated_synthetic_evidence_only_no_production_promotion",
    }


def _leak_needles(
    events: Sequence[StructuredLogEvent],
    scenarios: Sequence[B01Scenario],
) -> tuple[str, ...]:
    values = {
        *(event.event_id for event in events),
        *(event.content_document() for event in events),
        *(scenario.task.task_id for scenario in scenarios),
        *(scenario.task.descriptor.lexical_query() for scenario in scenarios),
        *(
            scenario.oracle.expected_action
            for scenario in scenarios
            if scenario.oracle.expected_action is not None
        ),
    }
    return tuple(sorted(values))


def assert_identifier_safe(
    rendered: str,
    events: Sequence[StructuredLogEvent],
    scenarios: Sequence[B01Scenario],
) -> None:
    """Fail if raw fixture records, labels, identifiers, or queries enter a report."""

    leaked = [value for value in _leak_needles(events, scenarios) if value in rendered]
    if leaked:
        raise ValueError("B01 report contains raw fixture data, labels, ids, or queries")


def run_fixture(
    work_dir: Path,
    *,
    repeats: int | None = None,
) -> dict[str, Any]:
    events, scenarios, frozen_config = load_fixture_bundle()
    config = (
        frozen_config
        if repeats is None
        else B01Config(
            repeats=repeats,
            frozen_clock=frozen_config.frozen_clock,
            context_budget_chars=frozen_config.context_budget_chars,
            max_selected_events=frozen_config.max_selected_events,
            max_operations=frozen_config.max_operations,
            max_events_scanned_per_operation=(
                frozen_config.max_events_scanned_per_operation
            ),
            max_results_per_operation=frozen_config.max_results_per_operation,
            confirmatory_partition=frozen_config.confirmatory_partition,
            minimum_confirmatory_caos_gain=(
                frozen_config.minimum_confirmatory_caos_gain
            ),
            maximum_operation_premium=frozen_config.maximum_operation_premium,
            combination_recipe=frozen_config.combination_recipe,
        )
    )
    if config.repeats < 1:
        raise ValueError("repeats must be positive")
    conditions: tuple[B01Condition, ...] = (
        RetrievalCondition(
            NoMemoryBaseline(),
            operation_name="no_memory",
            count_operation=False,
        ),
        RetrievalCondition(
            StableObservationLogBaseline(config.context_budget_chars),
            operation_name="stable_current_state_lexical_search",
        ),
        NativeProgrammaticCondition(config.programmatic_limits),
        RetrievalCondition(
            AtcRetrievalAdapter(
                work_dir / "atc",
                context_budget_chars=config.context_budget_chars,
            ),
            operation_name="atc_retrieval_v3",
        ),
        FrozenProgrammaticAtcCombination(work_dir / "combination", config),
    )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "fixture_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "event_schema": "atc.memory-lab.structured-event.v1",
        "adapter_abi": "atc.memory-lab.retrieval-adapter.v1",
        "event_count": len(events),
        "task_count": len(scenarios),
        "repeats": config.repeats,
        "budgets": {
            "frozen_clock": config.frozen_clock,
            "context_budget_chars": config.context_budget_chars,
            "max_selected_events": config.max_selected_events,
            "max_operations": config.max_operations,
            "max_events_scanned_per_operation": (
                config.max_events_scanned_per_operation
            ),
            "max_results_per_operation": config.max_results_per_operation,
        },
        "combination_recipe": config.combination_recipe,
        "environment": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
            "machine": platform.machine(),
            "clock": "time.perf_counter",
            "logical_clock": config.frozen_clock,
            "concurrency": 1,
            "cache_state": "process_warm_os_cache_uncontrolled",
        },
        "boundary": {
            "operator_core_touched": False,
            "personal_context_used": False,
            "external_code_used": False,
            "network_service_used": False,
            "provider_or_model_used": False,
            "canonical_state_written": False,
            "oracle_crossed_adapter_boundary": False,
        },
        "validity_limitations": [
            "not_a_pro_long_reproduction_no_equivalent_agent_action_model",
            "frozen_hand_authored_dsl_not_arbitrary_program_synthesis",
            "descriptor_strategy_and_fixture_oracle_were_co_designed",
            "query_dsl_supports_only_latest_route_and_threshold_route",
            "unsupported_strategies_abstain_without_atc_combination_fallback",
            "text_conditions_receive_content_document_plus_structural_adapter_metadata",
            "content_document_omits_identity_scope_supersession_and_expiry",
            "current_atc_internal_work_not_normalized_to_dsl_operations",
            "external_operation_counts_are_not_comparative_compute_measurements",
            "deterministic_common_action_executor_no_answer_model",
            "confirmatory_partition_changes_values_not_supported_dsl_grammar",
            "wave2_horizon_recorded_pro_long_repository_404_at_research_cutoff",
            "pro_long_agent_written_python_action_model_not_exercised",
            "sanitized_synthetic_small_fixture",
            "single_platform_wall_clock_latency_is_machine_specific",
            "isolated_research_not_implementation_acceptance",
        ],
        "conditions": {
            condition.manifest.adapter_id: _evaluate_condition(
                condition,
                events,
                scenarios,
                config,
            )
            for condition in conditions
        },
    }
    if tuple(report["conditions"]) != CONDITION_ORDER:
        raise RuntimeError("B01 condition order changed from the frozen comparison")
    report["decision"] = _decision(report, config)
    assert_identifier_safe(json.dumps(report, sort_keys=True), events, scenarios)
    report["identifier_leak_scan"] = {
        "passed": True,
        "event_documents_checked": len(events),
        "event_identifiers_checked": len(events),
        "task_identifiers_checked": len(scenarios),
        "full_task_queries_checked": len(scenarios),
        "expected_action_labels_checked": sum(
            scenario.oracle.expected_action is not None for scenario in scenarios
        ),
    }
    markdown = render_markdown_report(report)
    assert_identifier_safe(markdown, events, scenarios)
    return report


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# ATC Memory Lab Wave 3 B01",
        "",
        (
            f"Fixture `{report['fixture_sha256']}`; config `{report['config_sha256']}`; "
            f"{report['event_count']} events; {report['task_count']} tasks; "
            f"{report['repeats']} deterministic repeats."
        ),
        "",
        (
            "This is **not a reproduction of PRO-LONG**: no equivalent coding-agent "
            "action model, game environment, model, provider, or arbitrary program "
            "synthesis was exercised. The Wave 2 horizon recorded the linked repository "
            "as HTTP 404 at its research cutoff, and the paper setup allowed agent-written "
            "Python that this frozen DSL does not exercise."
        ),
        "",
        "| Condition | All CAOS | Confirm CAOS | Action | Recall | Disclosure chars | "
        "Ops/task | Forbidden | Decision context |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for condition_id in CONDITION_ORDER:
        result = report["conditions"][condition_id]
        metrics = result["metrics"]
        confirm = result["partitions"]["confirmatory"]
        lines.append(
            f"| `{condition_id}` | {metrics['caos_rate']:.3f} | "
            f"{confirm['caos_rate']:.3f} | {confirm['action_accuracy']:.3f} | "
            f"{confirm['mean_evidence_group_recall']:.3f} | "
            f"{confirm['mean_disclosure_chars']:.3f} | "
            f"{confirm['mean_operations_per_task']:.3f} | "
            f"{confirm['forbidden_output_count']} | bounded synthetic comparison |"
        )
    decision = report["decision"]
    lines.extend(
        [
            "",
            "## Frozen decision",
            "",
            f"- State: `{decision['state']}`.",
            (
                "- Programmatic confirmatory CAOS gain over stable lexical: "
                f"`{decision['confirmatory_caos_gain']:.3f}`."
            ),
            (
                "- Programmatic operation premium over stable lexical: "
                f"`{decision['confirmatory_operation_premium']}`."
            ),
            f"- Reason codes: `{', '.join(decision['reason_codes'])}`.",
            (
                "- Scope: this kill applies only to the bounded hand-authored B01 DSL "
                "under external-operation accounting. DSL reads were counted against one "
                "top-level lexical adapter call while lexical/ATC internal work was not "
                "normalized, so this result does not establish comparative compute "
                "efficiency and does not falsify PRO-LONG or general programmatic "
                "inspection."
            ),
            (
                "- Frozen combination disposition: "
                f"`{decision['combination_disposition']}`."
            ),
            "",
            "## Failure cases",
            "",
        ]
    )
    for condition_id in CONDITION_ORDER:
        cases = report["conditions"][condition_id]["failure_cases"]
        if not cases:
            lines.append(f"- `{condition_id}`: none.")
            continue
        rendered = "; ".join(
            f"task-index-{case['task_index']} ({', '.join(case['reason_codes'])})"
            for case in cases
        )
        lines.append(f"- `{condition_id}`: {rendered}.")
    lines.extend(
        [
            "",
            "## Planner and validity limitations",
            "",
            (
                "- The programmatic reader uses a frozen hand-authored query DSL for exact "
                "filters, current-state resolution, latest selection, bounded windows, "
                "counts, and policy joins. It is not arbitrary program synthesis."
            ),
            (
                "- The descriptor vocabulary, fixture, common executor, and oracle were "
                "co-designed; confirmatory cases change symbolic values, not the supported "
                "DSL grammar. Task-specific IDs and gold labels were not adapter-visible."
            ),
            (
                "- The DSL supports only `latest_route` and `threshold_route`; unsupported "
                "strategies abstain unless the separately frozen combination invokes ATC."
            ),
            (
                "- Every condition received the same sanitized events, frozen logical "
                "clock, descriptor values, character cap, result cap, and five-operation "
                "ceiling. Text-only conditions received the same content document plus "
                "identity, scope, supersession, and expiry as adapter metadata."
            ),
            (
                "- Core remained authoritative. No operator Core, personal context, "
                "external code, network service, provider, or model was used."
            ),
            (
                "- These are isolated deterministic synthetic results, not production "
                "implementation acceptance or a general memory claim."
            ),
            "",
            "## Identifier-leak scan",
            "",
            (
                "- Raw event documents, expected-action labels, event IDs, task IDs, "
                "and complete task-query strings: `passed`."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--repeats", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.repeats is not None and args.repeats < 1:
        raise ValueError("--repeats must be positive")

    def execute(work_dir: Path) -> dict[str, Any]:
        return run_fixture(work_dir, repeats=args.repeats)

    if args.work_dir is not None:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        report = execute(args.work_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="atc-memory-lab-b01-") as temporary:
            report = execute(Path(temporary))
    rendered_json = json.dumps(report, indent=2, sort_keys=True)
    rendered_markdown = render_markdown_report(report)
    events, scenarios, _ = load_fixture_bundle()
    assert_identifier_safe(rendered_json, events, scenarios)
    assert_identifier_safe(rendered_markdown, events, scenarios)
    if args.output is None:
        print(rendered_json)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            f"{rendered_json}\n",
            encoding="utf-8",
            newline="\n",
        )
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            rendered_markdown,
            encoding="utf-8",
            newline="\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
