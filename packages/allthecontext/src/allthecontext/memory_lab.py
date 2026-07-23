"""Provider-neutral contracts and metrics for deterministic memory benchmarks.

The lab operates on an already-authorized, immutable snapshot. Adapters can
rank object identifiers and report resource use, but they cannot establish or
mutate canonical Core state through this interface.
"""

from __future__ import annotations

import hashlib
import math
import platform
import re
import statistics
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

MEMORY_OBJECT_SCHEMA = "atc.memory-object.v1"
ADAPTER_ABI = "atc.memory-lab.retrieval-adapter.v1"
REPORT_SCHEMA = "atc.memory-lab.report.v2"
_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(UTC)


def _tokens(value: str) -> frozenset[str]:
    return frozenset(match.group(0).casefold() for match in _WORD_RE.finditer(value))


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


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


@dataclass(frozen=True, slots=True)
class MemoryObject:
    """One provider-neutral memory object in an authorized lab snapshot."""

    object_id: str
    kind: str
    content: str
    scopes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    valid_from: str | None = None
    expires_at: str | None = None
    supersedes: str | None = None
    explicit_user_statement: bool = False
    schema: str = MEMORY_OBJECT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MEMORY_OBJECT_SCHEMA:
            raise ValueError(f"unsupported memory object schema: {self.schema}")
        if not self.object_id.strip() or not self.kind.strip() or not self.content.strip():
            raise ValueError("memory object id, kind, and content must be non-blank")
        valid_from = _instant(self.valid_from) if self.valid_from is not None else None
        expires_at = _instant(self.expires_at) if self.expires_at is not None else None
        if valid_from is not None and expires_at is not None and expires_at <= valid_from:
            raise ValueError("expires_at must be later than valid_from")


@dataclass(frozen=True, slots=True)
class RetrievalTask:
    """A task-level retrieval judgment with set-sufficiency expectations."""

    task_id: str
    query: str
    evaluated_at: str
    limit: int
    evidence_groups: tuple[frozenset[str], ...] = ()
    forbidden_ids: frozenset[str] = frozenset()
    scopes: tuple[str, ...] = ()
    current_project: str | None = None
    context_budget_chars: int | None = None

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task id must be non-blank")
        if self.limit < 1:
            raise ValueError("task limit must be positive")
        if self.context_budget_chars is not None and self.context_budget_chars < 1:
            raise ValueError("context character budget must be positive")
        _instant(self.evaluated_at)
        if any(not group for group in self.evidence_groups):
            raise ValueError("evidence groups must be non-empty")

    @property
    def relevant_ids(self) -> frozenset[str]:
        return frozenset().union(*self.evidence_groups)

    @property
    def expects_abstention(self) -> bool:
        return not self.evidence_groups


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    """Stable adapter identity and declared provider/data-flow behavior."""

    adapter_id: str
    name: str
    version: str
    provider: str | None = None
    network_access: bool = False
    data_egress: tuple[str, ...] = ()
    writes_canonical_state: bool = False
    abi: str = ADAPTER_ABI

    def __post_init__(self) -> None:
        if self.abi != ADAPTER_ABI:
            raise ValueError(f"unsupported adapter ABI: {self.abi}")
        if not self.adapter_id.strip() or not self.name.strip() or not self.version.strip():
            raise ValueError("adapter id, name, and version must be non-blank")
        if self.writes_canonical_state:
            raise ValueError("Memory Lab adapters cannot write canonical state")
        if not self.network_access and self.data_egress:
            raise ValueError("data egress requires declared network access")


@dataclass(frozen=True, slots=True)
class AdapterUsage:
    """Provider-neutral accounting reported by an adapter invocation."""

    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    monetary_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if min(self.model_calls, self.input_tokens, self.output_tokens) < 0:
            raise ValueError("usage counts must be non-negative")
        if not math.isfinite(self.monetary_cost_usd) or self.monetary_cost_usd < 0:
            raise ValueError("monetary cost must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class PreparationReceipt:
    """Adapter-reported preparation cost that contains no memory content."""

    storage_bytes: int = 0
    usage: AdapterUsage = AdapterUsage()

    def __post_init__(self) -> None:
        if self.storage_bytes < 0:
            raise ValueError("storage size must be non-negative")


@dataclass(frozen=True, slots=True)
class RankedMemory:
    """One ranked object identifier; content never crosses the result ABI."""

    object_id: str
    score: float | None = None

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise ValueError("ranked object id must be non-blank")
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError("ranked score must be finite")


@dataclass(frozen=True, slots=True)
class RetrievalReceipt:
    """One adapter result with explicit abstention and resource accounting."""

    items: tuple[RankedMemory, ...]
    abstained: bool
    usage: AdapterUsage = AdapterUsage()

    def __post_init__(self) -> None:
        if self.abstained == bool(self.items):
            raise ValueError("abstained must be true exactly when no items are returned")


@dataclass(frozen=True, slots=True)
class BenchmarkMetadata:
    """Identifier-safe configuration and validity notes for one lab condition."""

    source_representation: str
    selection_mode: str
    context_budget_chars: int | None = None
    max_files_scanned: int | None = None
    max_bytes_scanned: int | None = None
    validity_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_representation.strip() or not self.selection_mode.strip():
            raise ValueError("benchmark metadata modes must be non-blank")
        for value in (
            self.context_budget_chars,
            self.max_files_scanned,
            self.max_bytes_scanned,
        ):
            if value is not None and value < 1:
                raise ValueError("benchmark metadata limits must be positive")
        if any(not item.strip() for item in self.validity_limitations):
            raise ValueError("validity limitation codes must be non-blank")


@runtime_checkable
class MemoryLabAdapter(Protocol):
    """Read-only adapter seam for simple, ATC, and future competitor systems."""

    @property
    def manifest(self) -> AdapterManifest: ...

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt: ...

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt: ...

    def close(self) -> None: ...


@runtime_checkable
class DescribedMemoryLabAdapter(Protocol):
    """Optional identifier-safe benchmark description implemented by lab controls."""

    @property
    def benchmark_metadata(self) -> BenchmarkMetadata: ...


class DeterministicLexicalBaseline:
    """Small token-overlap baseline with deterministic temporal/scope filtering."""

    manifest = AdapterManifest(
        adapter_id="deterministic-token-overlap",
        name="Deterministic token-overlap baseline",
        version="1",
    )
    benchmark_metadata = BenchmarkMetadata(
        source_representation="current_memory_objects",
        selection_mode="token_overlap_with_scope_time_and_supersession",
        validity_limitations=("lexical_only", "retrieval_only_no_reader"),
    )

    def __init__(self) -> None:
        self._objects: tuple[MemoryObject, ...] = ()

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        self._objects = tuple(objects)
        storage = sum(len(item.content.encode("utf-8")) for item in self._objects)
        return PreparationReceipt(storage_bytes=storage)

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        effective_at = _instant(task.evaluated_at)
        query_tokens = _tokens(task.query)
        active = tuple(item for item in self._objects if self._eligible(item, task, effective_at))
        superseded = {item.supersedes for item in active if item.supersedes is not None}
        ranked: list[RankedMemory] = []
        for item in active:
            if item.object_id in superseded:
                continue
            document_tokens = _tokens(
                " ".join((item.content, item.kind, *item.tags, *item.scopes))
            )
            overlap = len(query_tokens & document_tokens)
            if overlap:
                score = overlap / max(1, len(query_tokens))
                ranked.append(RankedMemory(item.object_id, round(score, 12)))
        ranked.sort(key=lambda item: item.object_id)
        ranked.sort(key=lambda item: item.score or 0.0, reverse=True)
        selected = tuple(ranked[: task.limit])
        return RetrievalReceipt(items=selected, abstained=not selected)

    @staticmethod
    def _eligible(
        item: MemoryObject,
        task: RetrievalTask,
        effective_at: datetime,
    ) -> bool:
        if task.scopes and not set(task.scopes).intersection(item.scopes):
            return False
        if item.valid_from is not None and _instant(item.valid_from) > effective_at:
            return False
        return item.expires_at is None or _instant(item.expires_at) > effective_at

    def close(self) -> None:
        self._objects = ()


class NoMemoryBaseline:
    """Control adapter that always abstains and retains no memory objects."""

    manifest = AdapterManifest(
        adapter_id="no-memory",
        name="No durable memory control",
        version="1",
    )
    benchmark_metadata = BenchmarkMetadata(
        source_representation="none",
        selection_mode="always_abstain",
        validity_limitations=("control_only", "retrieval_only_no_reader"),
    )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        _ = objects
        return PreparationReceipt()

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        _ = task
        return RetrievalReceipt(items=(), abstained=True)

    def close(self) -> None:
        return None


def _usage_dict(usage: AdapterUsage) -> dict[str, int | float]:
    return {
        "model_calls": usage.model_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "monetary_cost_usd": round(usage.monetary_cost_usd, 12),
    }


def _add_usage(left: AdapterUsage, right: AdapterUsage) -> AdapterUsage:
    return AdapterUsage(
        model_calls=left.model_calls + right.model_calls,
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        monetary_cost_usd=left.monetary_cost_usd + right.monetary_cost_usd,
    )


def _task_report(
    task_index: int,
    task: RetrievalTask,
    objects_by_id: dict[str, MemoryObject],
    receipts: Sequence[RetrievalReceipt],
    latencies_ms: Sequence[float],
) -> dict[str, Any]:
    first = receipts[0]
    ids = tuple(item.object_id for item in first.items)
    unique_ids = set(ids)
    unknown_ids = unique_ids - objects_by_id.keys()
    duplicate_count = len(ids) - len(unique_ids)
    over_limit_count = max(0, len(ids) - task.limit)
    contract_violations = len(unknown_ids) + duplicate_count + over_limit_count
    forbidden_count = len(unique_ids & task.forbidden_ids)
    group_hits = sum(bool(unique_ids & group) for group in task.evidence_groups)
    group_recall = group_hits / len(task.evidence_groups) if task.evidence_groups else 1.0
    known_ids = unique_ids & objects_by_id.keys()
    relevant_returned = known_ids & task.relevant_ids
    precision = len(relevant_returned) / len(known_ids) if known_ids else 0.0
    first_relevant = next(
        (rank for rank, object_id in enumerate(ids, 1) if object_id in task.relevant_ids),
        None,
    )
    reciprocal_rank = 0.0 if first_relevant is None else 1.0 / first_relevant
    abstention_correct = first.abstained if task.expects_abstention else None
    disclosure_chars = sum(len(objects_by_id[item].content) for item in known_ids)
    irrelevant_disclosure_chars = sum(
        len(objects_by_id[item].content) for item in known_ids - task.relevant_ids
    )
    budget_violation_count = int(
        task.context_budget_chars is not None
        and disclosure_chars > task.context_budget_chars
    )
    deterministic = all(receipt.items == first.items for receipt in receipts[1:])
    task_success = (
        (first.abstained if task.expects_abstention else group_recall == 1.0)
        and forbidden_count == 0
        and contract_violations == 0
        and budget_violation_count == 0
        and deterministic
    )
    safe_ordinals = {
        object_id: f"object-{ordinal:06d}"
        for ordinal, object_id in enumerate(objects_by_id)
    }
    ranking_shape = "\n".join(safe_ordinals.get(object_id, "unknown-object") for object_id in ids)
    ranking_fingerprint = hashlib.sha256(ranking_shape.encode("utf-8")).hexdigest()
    total_usage = AdapterUsage()
    for receipt in receipts:
        total_usage = _add_usage(total_usage, receipt.usage)
    failure_reason_codes: list[str] = []
    if task.expects_abstention and not first.abstained:
        failure_reason_codes.append("abstention_mismatch")
    if not task.expects_abstention and group_recall < 1.0:
        failure_reason_codes.append("required_evidence_missing")
    if forbidden_count:
        failure_reason_codes.append("forbidden_output")
    if contract_violations:
        failure_reason_codes.append("adapter_contract_violation")
    if budget_violation_count:
        failure_reason_codes.append("context_budget_exceeded")
    if not deterministic:
        failure_reason_codes.append("nondeterministic_ranking")
    return {
        "task_index": task_index,
        "returned_count": len(ids),
        "ranking_fingerprint": ranking_fingerprint,
        "task_success": task_success,
        "evidence_group_recall": round(group_recall, 6),
        "precision": round(precision, 6),
        "reciprocal_rank": round(reciprocal_rank, 6),
        "abstention_correct": abstention_correct,
        "forbidden_output_count": forbidden_count,
        "contract_violation_count": contract_violations,
        "budget_violation_count": budget_violation_count,
        "disclosure_chars": disclosure_chars,
        "irrelevant_disclosure_chars": irrelevant_disclosure_chars,
        "repeat_deterministic": deterministic,
        "failure_reason_codes": failure_reason_codes,
        "latency": {
            "p50_ms": round(_percentile(latencies_ms, 0.50), 6),
            "p95_ms": round(_percentile(latencies_ms, 0.95), 6),
        },
        "usage": _usage_dict(total_usage),
    }


def evaluate_adapter(
    adapter: MemoryLabAdapter,
    objects: Sequence[MemoryObject],
    tasks: Sequence[RetrievalTask],
    *,
    repeats: int = 3,
) -> dict[str, Any]:
    """Evaluate one adapter without logging or returning memory content."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    objects_by_id = {item.object_id: item for item in objects}
    if len(objects_by_id) != len(objects):
        raise ValueError("memory object ids must be unique")
    for task in tasks:
        unknown_gold = task.relevant_ids - objects_by_id.keys()
        if unknown_gold:
            raise ValueError(f"task {task.task_id} references unknown evidence ids")

    preparation_started = time.perf_counter()
    preparation = adapter.prepare(tuple(objects))
    preparation_ms = (time.perf_counter() - preparation_started) * 1_000
    task_reports: list[dict[str, Any]] = []
    all_latencies_ms: list[float] = []
    total_usage = preparation.usage
    try:
        for task_index, task in enumerate(tasks):
            receipts: list[RetrievalReceipt] = []
            latencies: list[float] = []
            for _ in range(repeats):
                started = time.perf_counter()
                receipt = adapter.retrieve(task)
                latency_ms = (time.perf_counter() - started) * 1_000
                latencies.append(latency_ms)
                all_latencies_ms.append(latency_ms)
                receipts.append(receipt)
                total_usage = _add_usage(total_usage, receipt.usage)
            task_reports.append(
                _task_report(task_index, task, objects_by_id, receipts, latencies)
            )
    finally:
        adapter.close()

    abstention = [
        bool(report["abstention_correct"])
        for report in task_reports
        if report["abstention_correct"] is not None
    ]
    benchmark_metadata = (
        asdict(adapter.benchmark_metadata)
        if isinstance(adapter, DescribedMemoryLabAdapter)
        else asdict(
            BenchmarkMetadata(
                source_representation="adapter_declared_snapshot",
                selection_mode="adapter_defined",
                validity_limitations=("retrieval_only_no_reader",),
            )
        )
    )
    failure_cases = [
        {
            "task_index": int(item["task_index"]),
            "reason_codes": list(item["failure_reason_codes"]),
        }
        for item in task_reports
        if item["failure_reason_codes"]
    ]
    return {
        "manifest": asdict(adapter.manifest),
        "benchmark": benchmark_metadata,
        "preparation": {
            "latency_ms": round(preparation_ms, 6),
            "storage_bytes": preparation.storage_bytes,
            "usage": _usage_dict(preparation.usage),
        },
        "metrics": {
            "task_success_rate": round(
                _mean([float(bool(item["task_success"])) for item in task_reports]), 6
            ),
            "mean_evidence_group_recall": round(
                _mean([float(item["evidence_group_recall"]) for item in task_reports]), 6
            ),
            "mean_reciprocal_rank": round(
                _mean([float(item["reciprocal_rank"]) for item in task_reports]), 6
            ),
            "mean_precision": round(
                _mean([float(item["precision"]) for item in task_reports]), 6
            ),
            "abstention_accuracy": round(_mean([float(item) for item in abstention]), 6),
            "forbidden_output_count": sum(
                int(item["forbidden_output_count"]) for item in task_reports
            ),
            "contract_violation_count": sum(
                int(item["contract_violation_count"]) for item in task_reports
            ),
            "budget_violation_count": sum(
                int(item["budget_violation_count"]) for item in task_reports
            ),
            "mean_disclosure_chars": round(
                _mean([float(item["disclosure_chars"]) for item in task_reports]), 6
            ),
            "mean_irrelevant_disclosure_chars": round(
                _mean(
                    [float(item["irrelevant_disclosure_chars"]) for item in task_reports]
                ),
                6,
            ),
            "deterministic_task_rate": round(
                _mean([float(bool(item["repeat_deterministic"])) for item in task_reports]),
                6,
            ),
            "retrieval_latency": {
                "p50_ms": round(_percentile(all_latencies_ms, 0.50), 6),
                "p95_ms": round(_percentile(all_latencies_ms, 0.95), 6),
                "p99_ms": round(_percentile(all_latencies_ms, 0.99), 6),
            },
            "usage": _usage_dict(total_usage),
        },
        "failure_cases": failure_cases,
        "tasks": task_reports,
    }


def run_memory_lab(
    objects: Sequence[MemoryObject],
    tasks: Sequence[RetrievalTask],
    adapters: Sequence[MemoryLabAdapter],
    *,
    fixture_sha256: str,
    repeats: int = 3,
) -> dict[str, Any]:
    """Run the same immutable corpus and task protocol against every adapter."""

    adapter_ids = [adapter.manifest.adapter_id for adapter in adapters]
    if len(adapter_ids) != len(set(adapter_ids)):
        raise ValueError("adapter ids must be unique")
    return {
        "schema": REPORT_SCHEMA,
        "fixture_sha256": fixture_sha256,
        "memory_object_schema": MEMORY_OBJECT_SCHEMA,
        "adapter_abi": ADAPTER_ABI,
        "object_count": len(objects),
        "task_count": len(tasks),
        "repeats": repeats,
        "environment": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
            "machine": platform.machine(),
            "clock": "time.perf_counter",
            "concurrency": 1,
            "cache_state": "process_warm_os_cache_uncontrolled",
        },
        "validity_limitations": [
            "sanitized_deterministic_fixture",
            "retrieval_only_no_answer_model",
            "no_action_or_caos_endpoint",
            "small_nonrepresentative_corpus",
            "simple_reference_conditions_not_implementation_acceptance",
            "wall_clock_latency_is_machine_specific",
            "storage_excludes_common_source_corpus",
        ],
        "adapters": {
            adapter.manifest.adapter_id: evaluate_adapter(
                adapter,
                objects,
                tasks,
                repeats=repeats,
            )
            for adapter in adapters
        },
    }
