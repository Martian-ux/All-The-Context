"""Deterministic longitudinal contracts for the bounded E01 Memory Lab slice.

This research module models symbolic lifecycle behavior in memory.  It does not
open a Core database, mutate production state, or define a production schema.
Adapters receive ordered events and task descriptors, but never oracle labels.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

LONGITUDINAL_ADAPTER_ABI = "atc.memory-reliability-lab.longitudinal-adapter.v1"
LONGITUDINAL_REPORT_SCHEMA = "atc.memory-reliability-lab.e01-report.v1"
LONGITUDINAL_FIXTURE_SCHEMA = "atc.memory-reliability-lab.e01-fixture.v1"
FULL_SPEC_SCENARIO_COUNT = 18
REFERENCE_RULES = frozenset(
    {"authority", "currentness_invalidation", "applicability", "purge_closure"}
)

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9_.:-]*$")
_TOKEN_RE = re.compile(r"[A-Z0-9]+")
_STAGE_ORDER = {
    "capture": 0,
    "canonicalize": 1,
    "authorize": 2,
    "applicability": 3,
    "retrieve": 4,
    "compile": 5,
    "correct_forget": 6,
    "invalidate_rebuild": 7,
    "budget": 8,
}
_MEMORY_OPERATIONS = frozenset(
    {
        "set_claim",
        "correct_claim",
        "set_preference",
        "set_evidence",
        "set_procedure",
        "derive_artifact",
    }
)
_CONTROL_OPERATIONS = frozenset(
    {
        "evict_working_context",
        "apply_ranking_decay",
        "retire_procedure",
        "soft_delete",
        "restore",
        "purge",
    }
)
_AUTHORITATIVE_SOURCES = frozenset(
    {
        "host_attested_user_turn",
        "environment_observation",
        "tool_outcome",
        "user_confirmed_contract",
    }
)


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


def _symbol(value: str, field_name: str) -> str:
    if _SYMBOL_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be an opaque uppercase symbol")
    return value


def _terms(*values: str | None) -> frozenset[str]:
    return frozenset(
        token for value in values if value is not None for token in _TOKEN_RE.findall(value.upper())
    )


def _fingerprint(values: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EpisodeBudget:
    """Equal output budget supplied to every condition."""

    max_items: int
    max_token_units: int

    def __post_init__(self) -> None:
        if self.max_items < 1 or self.max_token_units < 1:
            raise ValueError("episode budgets must be positive")

    @property
    def capacity(self) -> int:
        return min(self.max_items, self.max_token_units)


@dataclass(frozen=True, slots=True)
class LogicalEvent:
    """One adapter-visible symbolic event on the harness-owned frozen clock."""

    seq: int
    at: str
    principal: str
    source_class: str
    operation: str
    object_id: str
    topic: str | None = None
    role: str | None = None
    value: str | None = None
    project: str | None = None
    domain: str | None = None
    applies_to: str | None = None
    supersedes: str | None = None
    dependency: str | None = None

    def __post_init__(self) -> None:
        if self.seq < 1:
            raise ValueError("event sequence must be positive")
        _instant(self.at)
        _symbol(self.principal, "principal")
        _symbol(self.object_id, "object_id")
        if self.operation not in _MEMORY_OPERATIONS | _CONTROL_OPERATIONS:
            raise ValueError(f"unsupported longitudinal operation: {self.operation}")
        for name in (
            "topic",
            "role",
            "value",
            "project",
            "domain",
            "applies_to",
            "supersedes",
            "dependency",
        ):
            value = getattr(self, name)
            if value is not None:
                _symbol(value, name)
        if self.operation in _MEMORY_OPERATIONS and (
            self.topic is None or self.role is None or self.value is None
        ):
            raise ValueError("memory events require topic, role, and value")


@dataclass(frozen=True, slots=True)
class CheckpointDescriptor:
    """Adapter-visible task metadata with no gold or forbidden labels."""

    checkpoint_id: str
    after_seq: int
    at: str
    principal: str
    task_class: str
    query_terms: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    project: str | None = None
    domain: str | None = None

    def __post_init__(self) -> None:
        _symbol(self.checkpoint_id, "checkpoint_id")
        _symbol(self.principal, "principal")
        _symbol(self.task_class, "task_class")
        _instant(self.at)
        if self.after_seq < 1:
            raise ValueError("checkpoint sequence must be positive")
        if not self.query_terms or not self.allowed_roles:
            raise ValueError("checkpoints require query terms and allowed roles")
        for value in (*self.query_terms, *self.allowed_roles):
            _symbol(value, "checkpoint symbol")
        if self.project is not None:
            _symbol(self.project, "project")
        if self.domain is not None:
            _symbol(self.domain, "domain")


@dataclass(frozen=True, slots=True)
class FailureAttribution:
    """Harness-only deterministic attribution for one forbidden influence."""

    stage: str
    code: str

    def __post_init__(self) -> None:
        if self.stage not in _STAGE_ORDER:
            raise ValueError(f"unknown failure stage: {self.stage}")
        _symbol(self.code, "failure code")


@dataclass(frozen=True, slots=True)
class CheckpointOracle:
    """Gold labels retained by the harness and never passed to adapters."""

    required_values: frozenset[str] = frozenset()
    forbidden: Mapping[str, FailureAttribution] = field(default_factory=dict)
    expects_abstention: bool = False
    inventory_states: Mapping[str, str] = field(default_factory=dict)
    inventory_absent: Mapping[str, FailureAttribution] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.expects_abstention and self.required_values:
            raise ValueError("abstention checkpoints cannot require selected values")
        for value in self.required_values:
            _symbol(value, "required value")
        for object_id, state in self.inventory_states.items():
            _symbol(object_id, "inventory object")
            _symbol(state, "inventory state")
        for object_id in self.inventory_absent:
            _symbol(object_id, "absent inventory object")


@dataclass(frozen=True, slots=True)
class LongitudinalScenario:
    """One ordered episode with a single bounded evaluation checkpoint."""

    scenario_id: str
    capabilities: tuple[str, ...]
    events: tuple[LogicalEvent, ...]
    checkpoint: CheckpointDescriptor
    oracle: CheckpointOracle

    def __post_init__(self) -> None:
        _symbol(self.scenario_id, "scenario_id")
        if not self.capabilities or not self.events:
            raise ValueError("scenarios require capabilities and events")
        if [event.seq for event in self.events] != list(range(1, len(self.events) + 1)):
            raise ValueError("scenario events must have contiguous ordered sequences")
        if [event.at for event in self.events] != sorted(event.at for event in self.events):
            raise ValueError("scenario event timestamps must be monotonic")
        if self.checkpoint.after_seq != self.events[-1].seq:
            raise ValueError("the bounded slice evaluates after the final event")
        if _instant(self.checkpoint.at) < _instant(self.events[-1].at):
            raise ValueError("checkpoint cannot precede the final event")


@dataclass(frozen=True, slots=True)
class ConditionManifest:
    """Stable identity and authority declaration for one research condition."""

    condition_id: str
    name: str
    lifecycle_model: str
    network_access: bool = False
    touches_production_core: bool = False
    writes_canonical_state: bool = False
    abi: str = LONGITUDINAL_ADAPTER_ABI

    def __post_init__(self) -> None:
        if self.abi != LONGITUDINAL_ADAPTER_ABI:
            raise ValueError(f"unsupported longitudinal adapter ABI: {self.abi}")
        if self.network_access or self.touches_production_core or self.writes_canonical_state:
            raise ValueError("the local E01 slice must be offline and noncanonical")


@dataclass(frozen=True, slots=True)
class SelectedMemory:
    """Internal evaluation selection; identifiers never enter reusable reports."""

    object_id: str
    value: str
    role: str


@dataclass(frozen=True, slots=True)
class CheckpointReceipt:
    """One bounded adapter result."""

    selected: tuple[SelectedMemory, ...]
    abstained: bool
    token_units: int

    def __post_init__(self) -> None:
        if self.abstained == bool(self.selected):
            raise ValueError("abstained must be true exactly when no items are selected")
        if self.token_units != len(self.selected):
            raise ValueError("the symbolic slice charges one token unit per selected item")


@runtime_checkable
class LongitudinalMemoryAdapter(Protocol):
    """Small reset/present/checkpoint ABI for this executable research slice."""

    @property
    def manifest(self) -> ConditionManifest: ...

    def reset(self, run_identity: str, principal: str, frozen_clock: str) -> None: ...

    def present(self, event: LogicalEvent) -> None: ...

    def checkpoint(
        self,
        descriptor: CheckpointDescriptor,
        budget: EpisodeBudget,
    ) -> CheckpointReceipt: ...

    def inventory(self) -> Mapping[str, str]: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class _MemoryEntry:
    object_id: str
    topic: str
    role: str
    value: str
    principal: str
    source_class: str
    seq: int
    project: str | None
    domain: str | None
    applies_to: str | None
    dependency: str | None
    state: str = "CURRENT"

    @classmethod
    def from_event(cls, event: LogicalEvent) -> _MemoryEntry:
        assert event.topic is not None
        assert event.role is not None
        assert event.value is not None
        return cls(
            object_id=event.object_id,
            topic=event.topic,
            role=event.role,
            value=event.value,
            principal=event.principal,
            source_class=event.source_class,
            seq=event.seq,
            project=event.project,
            domain=event.domain,
            applies_to=event.applies_to,
            dependency=event.dependency,
        )

    def search_terms(self) -> frozenset[str]:
        return _terms(
            self.topic,
            self.role,
            self.value,
            self.project,
            self.domain,
            self.applies_to,
        )


class NoMemoryLongitudinalAdapter:
    """Control condition that observes the episode but retains no memory."""

    manifest = ConditionManifest(
        condition_id="simple_no_memory",
        name="No durable memory",
        lifecycle_model="none",
    )

    def reset(self, run_identity: str, principal: str, frozen_clock: str) -> None:
        _symbol(run_identity, "run identity")
        _symbol(principal, "principal")
        _instant(frozen_clock)

    def present(self, event: LogicalEvent) -> None:
        _ = event

    def checkpoint(
        self,
        descriptor: CheckpointDescriptor,
        budget: EpisodeBudget,
    ) -> CheckpointReceipt:
        _ = (descriptor, budget)
        return CheckpointReceipt(selected=(), abstained=True, token_units=0)

    def inventory(self) -> Mapping[str, str]:
        return {}

    def close(self) -> None:
        return None


class AppendLogSearchAdapter:
    """Authorized append-only exact/lexical search with no current-state model."""

    manifest = ConditionManifest(
        condition_id="simple_append_log_search",
        name="Append-only event log search",
        lifecycle_model="append_only_no_canonical_current_state",
    )

    def __init__(self) -> None:
        self._principal = ""
        self._entries: list[_MemoryEntry] = []

    def reset(self, run_identity: str, principal: str, frozen_clock: str) -> None:
        _symbol(run_identity, "run identity")
        self._principal = _symbol(principal, "principal")
        _instant(frozen_clock)
        self._entries = []

    def present(self, event: LogicalEvent) -> None:
        if event.operation in _MEMORY_OPERATIONS:
            self._entries.append(_MemoryEntry.from_event(event))

    def checkpoint(
        self,
        descriptor: CheckpointDescriptor,
        budget: EpisodeBudget,
    ) -> CheckpointReceipt:
        query = _terms(*descriptor.query_terms)
        ranked = [
            entry
            for entry in self._entries
            if entry.principal == self._principal and query & entry.search_terms()
        ]
        ranked.sort(key=lambda entry: (-len(query & entry.search_terms()), -entry.seq))
        selected = tuple(
            SelectedMemory(entry.object_id, entry.value, entry.role)
            for entry in ranked[: budget.capacity]
        )
        return CheckpointReceipt(
            selected=selected,
            abstained=not selected,
            token_units=len(selected),
        )

    def inventory(self) -> Mapping[str, str]:
        return {entry.object_id: "RECORDED" for entry in self._entries}

    def close(self) -> None:
        self._entries = []
        self._principal = ""


class AtcGovernedReferenceAdapter:
    """In-memory reference model, not an execution of the production ATC Core."""

    def __init__(
        self,
        *,
        condition_id: str = "atc_governed_reference",
        enabled_rules: frozenset[str] = REFERENCE_RULES,
    ) -> None:
        unknown = enabled_rules - REFERENCE_RULES
        if unknown:
            raise ValueError(f"unknown governed reference rules: {sorted(unknown)}")
        self.manifest = ConditionManifest(
            condition_id=condition_id,
            name="ATC-governed symbolic reference model",
            lifecycle_model="generic_authority_currentness_applicability_and_purge_rules",
        )
        self.enabled_rules = enabled_rules
        self._principal = ""
        self._entries: dict[str, _MemoryEntry] = {}
        self._dependents: dict[str, set[str]] = {}
        self._purged_ids: set[str] = set()

    def reset(self, run_identity: str, principal: str, frozen_clock: str) -> None:
        _symbol(run_identity, "run identity")
        self._principal = _symbol(principal, "principal")
        _instant(frozen_clock)
        self._entries = {}
        self._dependents = {}
        self._purged_ids = set()

    def present(self, event: LogicalEvent) -> None:
        if event.operation in _MEMORY_OPERATIONS:
            if event.principal != self._principal:
                return
            if (
                "authority" in self.enabled_rules
                and event.source_class not in _AUTHORITATIVE_SOURCES
            ):
                return
            if event.object_id in self._purged_ids:
                return
            entry = _MemoryEntry.from_event(event)
            self._entries[event.object_id] = entry
            if event.dependency is not None:
                self._dependents.setdefault(event.dependency, set()).add(event.object_id)
            if event.supersedes is not None:
                previous = self._entries.get(event.supersedes)
                if previous is not None and "currentness_invalidation" in self.enabled_rules:
                    previous.state = "SUPERSEDED"
                if "currentness_invalidation" in self.enabled_rules:
                    self._invalidate_dependents(event.supersedes)
            return

        if event.principal != self._principal:
            return
        if "authority" in self.enabled_rules and event.source_class not in _AUTHORITATIVE_SOURCES:
            return
        if event.object_id in self._purged_ids and event.operation != "purge":
            return
        target = self._entries.get(event.object_id)
        if event.operation == "purge":
            self._purged_ids.add(event.object_id)
            if target is not None:
                target.state = "PURGED"
            if "purge_closure" in self.enabled_rules:
                self._purge_dependents(event.object_id)
        elif target is not None:
            if event.operation == "retire_procedure":
                target.state = "RETIRED"
            elif event.operation == "soft_delete":
                target.state = "DELETED"
            elif event.operation == "restore":
                target.state = "CURRENT"
            # Eviction and rank decay deliberately do not change canonical truth.

    def _invalidate_dependents(self, object_id: str) -> None:
        for dependent_id in self._dependents.get(object_id, set()):
            dependent = self._entries.get(dependent_id)
            if dependent is not None:
                dependent.state = "INVALIDATED"

    def _purge_dependents(self, object_id: str) -> None:
        for dependent_id in self._dependents.get(object_id, set()):
            dependent = self._entries.get(dependent_id)
            if dependent is not None:
                dependent.state = "PURGED"
                self._purged_ids.add(dependent_id)

    def _applicable(self, entry: _MemoryEntry, descriptor: CheckpointDescriptor) -> bool:
        if "applicability" not in self.enabled_rules:
            return True
        if entry.role not in descriptor.allowed_roles:
            return False
        if entry.project is not None and entry.project != descriptor.project:
            return False
        if entry.domain is not None and entry.domain != descriptor.domain:
            return False
        return entry.applies_to is None or entry.applies_to == descriptor.task_class

    def checkpoint(
        self,
        descriptor: CheckpointDescriptor,
        budget: EpisodeBudget,
    ) -> CheckpointReceipt:
        query = _terms(*descriptor.query_terms)
        ranked = [
            entry
            for entry in self._entries.values()
            if entry.state == "CURRENT"
            and entry.principal == self._principal
            and self._applicable(entry, descriptor)
            and query & entry.search_terms()
        ]
        ranked.sort(key=lambda entry: (-len(query & entry.search_terms()), -entry.seq))
        selected = tuple(
            SelectedMemory(entry.object_id, entry.value, entry.role)
            for entry in ranked[: budget.capacity]
        )
        return CheckpointReceipt(
            selected=selected,
            abstained=not selected,
            token_units=len(selected),
        )

    def inventory(self) -> Mapping[str, str]:
        return {
            object_id: entry.state
            for object_id, entry in self._entries.items()
            if entry.state != "PURGED"
        }

    def close(self) -> None:
        self._principal = ""
        self._entries = {}
        self._dependents = {}
        self._purged_ids = set()


def _failure_sort_key(attribution: FailureAttribution) -> tuple[int, str]:
    return (_STAGE_ORDER[attribution.stage], attribution.code)


def _evaluate_checkpoint(
    scenario_index: int,
    scenario: LongitudinalScenario,
    receipt: CheckpointReceipt,
    inventory: Mapping[str, str],
    budget: EpisodeBudget,
) -> dict[str, Any]:
    selected_ids = tuple(item.object_id for item in receipt.selected)
    selected_values = frozenset(item.value for item in receipt.selected)
    failures: list[FailureAttribution] = []
    forbidden_count = 0
    for item in receipt.selected:
        attribution = scenario.oracle.forbidden.get(item.object_id)
        if attribution is not None:
            failures.append(attribution)
            forbidden_count += 1
        elif item.value not in scenario.oracle.required_values:
            failures.append(FailureAttribution("compile", "EXCESS_DISCLOSURE"))
            forbidden_count += 1

    missing_required = scenario.oracle.required_values - selected_values
    if missing_required:
        failures.append(FailureAttribution("retrieve", "RETRIEVAL_MISS"))
    if scenario.oracle.expects_abstention and receipt.selected:
        failures.append(FailureAttribution("applicability", "HARMFUL_MEMORY_NON_ABSTENTION"))

    for object_id, expected_state in scenario.oracle.inventory_states.items():
        if inventory.get(object_id) != expected_state:
            failures.append(FailureAttribution("correct_forget", "FORGETTING_SEMANTIC_COLLAPSE"))
    for object_id, attribution in scenario.oracle.inventory_absent.items():
        if object_id in inventory:
            failures.append(attribution)
    if receipt.token_units > budget.max_token_units or len(receipt.selected) > budget.max_items:
        failures.append(FailureAttribution("budget", "BUDGET_ESCAPE"))

    unique_failures = sorted(set(failures), key=_failure_sort_key)
    primary = unique_failures[0] if unique_failures else None
    exact_success = not unique_failures and (
        receipt.abstained
        if scenario.oracle.expects_abstention
        else selected_values == scenario.oracle.required_values
    )
    safe_ordinals_by_id = {
        event.object_id: f"object-{ordinal:04d}" for ordinal, event in enumerate(scenario.events)
    }
    safe_ordinals = [
        safe_ordinals_by_id.get(object_id, "unknown-object") for object_id in selected_ids
    ]
    return {
        "scenario_index": scenario_index,
        "capabilities": sorted(scenario.capabilities),
        "selected_count": len(receipt.selected),
        "selection_fingerprint": _fingerprint(safe_ordinals),
        "token_units": receipt.token_units,
        "abstained": receipt.abstained,
        "expected_abstention": scenario.oracle.expects_abstention,
        "exact_current_authorized_state_success": exact_success,
        "forbidden_influence_count": forbidden_count,
        "failure_codes": [failure.code for failure in unique_failures],
        "primary_failure_stage": primary.stage if primary is not None else None,
        "primary_failure_code": primary.code if primary is not None else None,
    }


def _counter(values: Sequence[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def evaluate_longitudinal_adapter(
    adapter: LongitudinalMemoryAdapter,
    scenarios: Sequence[LongitudinalScenario],
    *,
    budget: EpisodeBudget,
    repeats: int = 2,
) -> dict[str, Any]:
    """Evaluate one adapter without exposing fixture identifiers or values."""

    if repeats < 2:
        raise ValueError("the deterministic slice requires at least two repeats")
    first_reports: list[dict[str, Any]] = []
    repeat_fingerprints: list[list[str]] = []
    presented_events = 0
    for repeat in range(repeats):
        current_fingerprints: list[str] = []
        for scenario_index, scenario in enumerate(scenarios):
            run_identity = f"RUN_{repeat:02d}_{scenario_index:02d}"
            adapter.reset(
                run_identity,
                scenario.checkpoint.principal,
                scenario.events[0].at,
            )
            for event in scenario.events:
                adapter.present(event)
                presented_events += 1
            receipt = adapter.checkpoint(scenario.checkpoint, budget)
            report = _evaluate_checkpoint(
                scenario_index,
                scenario,
                receipt,
                adapter.inventory(),
                budget,
            )
            current_fingerprints.append(
                _fingerprint(
                    [
                        str(report["selection_fingerprint"]),
                        str(report["exact_current_authorized_state_success"]),
                        ",".join(report["failure_codes"]),
                    ]
                )
            )
            if repeat == 0:
                first_reports.append(report)
        repeat_fingerprints.append(current_fingerprints)
    adapter.close()

    deterministic = all(
        fingerprints == repeat_fingerprints[0] for fingerprints in repeat_fingerprints[1:]
    )
    successes = sum(
        bool(report["exact_current_authorized_state_success"]) for report in first_reports
    )
    abstention_reports = [report for report in first_reports if bool(report["expected_abstention"])]
    correction_reports = [
        report for report in first_reports if "correction" in report["capabilities"]
    ]
    forgetting_reports = [
        report for report in first_reports if "forgetting" in report["capabilities"]
    ]
    failure_codes = [str(code) for report in first_reports for code in report["failure_codes"]]
    first_failure_stages = [
        str(report["primary_failure_stage"])
        for report in first_reports
        if report["primary_failure_stage"] is not None
    ]
    denominator = len(first_reports)
    return {
        "manifest": asdict(adapter.manifest),
        "budget": asdict(budget),
        "accounting": {
            "presented_event_count": presented_events,
            "checkpoint_count": denominator * repeats,
            "model_calls": 0,
            "network_calls": 0,
            "monetary_cost_usd": 0.0,
        },
        "metrics": {
            "exact_current_authorized_state_success_count": successes,
            "exact_current_authorized_state_success_rate": round(
                successes / denominator if denominator else 0.0, 6
            ),
            "forbidden_influence_count": sum(
                int(report["forbidden_influence_count"]) for report in first_reports
            ),
            "unauthorized_influence_count": failure_codes.count("UNAUTHORIZED_INFLUENCE"),
            "authorized_but_inapplicable_count": sum(
                failure_codes.count(code)
                for code in (
                    "EPISTEMIC_ROLE_MISUSE",
                    "CROSS_PROJECT_DOMAIN_LEAKAGE",
                    "PROCEDURE_FALSE_TRANSFER",
                )
            ),
            "cross_project_domain_leakage_count": failure_codes.count(
                "CROSS_PROJECT_DOMAIN_LEAKAGE"
            ),
            "harmful_memory_non_abstention_count": failure_codes.count(
                "HARMFUL_MEMORY_NON_ABSTENTION"
            ),
            "purge_residue_count": failure_codes.count("PURGE_RESIDUE"),
            "correction_convergence_rate": round(
                sum(
                    bool(report["exact_current_authorized_state_success"])
                    for report in correction_reports
                )
                / len(correction_reports)
                if correction_reports
                else 0.0,
                6,
            ),
            "forgetting_semantics_rate": round(
                sum(
                    bool(report["exact_current_authorized_state_success"])
                    for report in forgetting_reports
                )
                / len(forgetting_reports)
                if forgetting_reports
                else 0.0,
                6,
            ),
            "harmful_abstention_accuracy": round(
                sum(
                    bool(report["exact_current_authorized_state_success"])
                    for report in abstention_reports
                )
                / len(abstention_reports)
                if abstention_reports
                else 0.0,
                6,
            ),
            "budget_escape_count": failure_codes.count("BUDGET_ESCAPE"),
            "repeat_deterministic": deterministic,
            "failure_code_counts": _counter(failure_codes),
            "first_failure_stage_counts": _counter(first_failure_stages),
        },
        "episodes": first_reports,
    }


def run_e01_slice(
    scenarios: Sequence[LongitudinalScenario],
    adapters: Sequence[LongitudinalMemoryAdapter],
    *,
    fixture_sha256: str,
    budget: EpisodeBudget,
    repeats: int = 2,
) -> dict[str, Any]:
    """Run the same partial E01 episodes and budget against every condition."""

    condition_ids = [adapter.manifest.condition_id for adapter in adapters]
    if len(condition_ids) != len(set(condition_ids)):
        raise ValueError("condition ids must be unique")
    results = {
        adapter.manifest.condition_id: evaluate_longitudinal_adapter(
            adapter,
            scenarios,
            budget=budget,
            repeats=repeats,
        )
        for adapter in adapters
    }
    governed = results.get("atc_governed_reference")
    append_log = results.get("simple_append_log_search")
    no_memory = results.get("simple_no_memory")
    bounded_hypotheses: dict[str, str] = {}
    if governed is not None and append_log is not None:
        bounded_hypotheses["append_log_equivalent_to_governed_reference"] = (
            "falsified_in_bounded_slice"
            if governed["metrics"]["exact_current_authorized_state_success_rate"]
            > append_log["metrics"]["exact_current_authorized_state_success_rate"]
            else "not_falsified"
        )
        bounded_hypotheses["append_log_matches_governed_harmful_abstention"] = (
            "falsified_in_bounded_slice"
            if governed["metrics"]["harmful_abstention_accuracy"]
            > append_log["metrics"]["harmful_abstention_accuracy"]
            else "not_falsified"
        )
    if governed is not None and no_memory is not None:
        bounded_hypotheses["no_memory_equivalent_to_governed_reference"] = (
            "falsified_in_bounded_slice"
            if governed["metrics"]["exact_current_authorized_state_success_rate"]
            > no_memory["metrics"]["exact_current_authorized_state_success_rate"]
            else "not_falsified"
        )
    ablation_results: dict[str, Any] = {}
    if governed is not None:
        governed_metrics = governed["metrics"]
        for condition_id, result in results.items():
            if not condition_id.startswith("ablation_without_"):
                continue
            ablated_metrics = result["metrics"]
            ablation_results[condition_id] = {
                "removed_rule": condition_id.removeprefix("ablation_without_"),
                "exact_success_rate_delta_from_reference": round(
                    float(governed_metrics["exact_current_authorized_state_success_rate"])
                    - float(ablated_metrics["exact_current_authorized_state_success_rate"]),
                    6,
                ),
                "forbidden_influence_delta_from_reference": (
                    int(ablated_metrics["forbidden_influence_count"])
                    - int(governed_metrics["forbidden_influence_count"])
                ),
                "purge_residue_delta_from_reference": (
                    int(ablated_metrics["purge_residue_count"])
                    - int(governed_metrics["purge_residue_count"])
                ),
                "harmful_non_abstention_delta_from_reference": (
                    int(ablated_metrics["harmful_memory_non_abstention_count"])
                    - int(governed_metrics["harmful_memory_non_abstention_count"])
                ),
            }
    return {
        "schema": LONGITUDINAL_REPORT_SCHEMA,
        "adapter_abi": LONGITUDINAL_ADAPTER_ABI,
        "fixture_sha256": fixture_sha256,
        "experiment": "E01",
        "execution_scope": {
            "kind": "smallest_executable_longitudinal_slice",
            "executed_scenario_count": len(scenarios),
            "full_spec_scenario_count": FULL_SPEC_SCENARIO_COUNT,
            "full_spec_executed": len(scenarios) == FULL_SPEC_SCENARIO_COUNT,
            "production_core_touched": False,
            "production_schema_added": False,
            "external_systems_exercised": False,
            "wall_clock_timing_measured": False,
            "production_core_semantics": "production_core_semantics_not_exercised",
            "governed_condition_identity": ("new_in_memory_reference_model_not_current_atc"),
        },
        "frozen_clock": {
            "wall_clock_access": False,
            "source": "event_supplied_utc_timestamps",
        },
        "equal_budget": asdict(budget),
        "repeats": repeats,
        "conditions": results,
        "rule_ablation_results": ablation_results,
        "bounded_hypotheses": bounded_hypotheses,
        "validity_risks": [
            "fixture_oracle_and_reference_rule_codesign",
            "small_hand_authored_symbolic_slice",
            "no_production_core_execution",
            "no_external_system_execution",
        ],
        "privacy": {
            "raw_context_in_report": False,
            "object_identifiers_in_report": False,
            "task_identifiers_in_report": False,
            "selection_values_in_report": False,
        },
    }


def report_contains_fixture_symbols(
    report: Mapping[str, Any],
    scenarios: Sequence[LongitudinalScenario],
) -> bool:
    """Return whether a reusable report leaked an event/checkpoint symbol."""

    rendered = json.dumps(report, sort_keys=True)
    forbidden = (
        {scenario.scenario_id for scenario in scenarios}
        | {
            value
            for scenario in scenarios
            for event in scenario.events
            for value in (
                event.object_id,
                event.value,
                event.topic,
                event.project,
                event.domain,
                event.applies_to,
            )
            if value is not None
        }
        | {scenario.checkpoint.checkpoint_id for scenario in scenarios}
        | {term for scenario in scenarios for term in scenario.checkpoint.query_terms}
    )
    return any(symbol in rendered for symbol in forbidden)
