"""Strong deterministic controls for the ATC Memory Lab baseline ladder.

All controls operate on the authorized immutable snapshot supplied by the lab.
They neither contact Core nor create canonical state. The local file-search
control exercises bounded file materialization and scanning only; it is not a
programmatic action-model or coding-agent search reproduction.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from allthecontext.memory_lab import (
    AdapterManifest,
    BenchmarkMetadata,
    MemoryObject,
    PreparationReceipt,
    RankedMemory,
    RetrievalReceipt,
    RetrievalTask,
    _instant,
    _tokens,
)


def _stored_content_bytes(objects: Iterable[MemoryObject]) -> int:
    return sum(len(item.content.encode("utf-8")) for item in objects)


def _project_scope_applies(item: MemoryObject, task: RetrievalTask) -> bool:
    if task.current_project is None:
        return True
    project_scopes = tuple(
        scope.removeprefix("project:")
        for scope in item.scopes
        if scope.startswith("project:")
    )
    return not project_scopes or task.current_project.casefold() in {
        scope.casefold() for scope in project_scopes
    }


def _eligible_event(
    item: MemoryObject,
    task: RetrievalTask,
    effective_at: datetime,
    *,
    current_only: bool,
) -> bool:
    if task.scopes and not set(task.scopes).intersection(item.scopes):
        return False
    if not _project_scope_applies(item, task):
        return False
    if item.valid_from is not None and _instant(item.valid_from) > effective_at:
        return False
    if current_only and item.expires_at is not None:
        return _instant(item.expires_at) > effective_at
    return True


def _budgeted(
    ranked: Iterable[tuple[MemoryObject, float | None]],
    *,
    limit: int,
    context_budget_chars: int,
) -> tuple[RankedMemory, ...]:
    selected: list[RankedMemory] = []
    disclosed_chars = 0
    for item, score in ranked:
        item_chars = len(item.content)
        if disclosed_chars + item_chars > context_budget_chars:
            continue
        selected.append(RankedMemory(item.object_id, score))
        disclosed_chars += item_chars
        if len(selected) == limit:
            break
    return tuple(selected)


def _lexical_score(task: RetrievalTask, item: MemoryObject) -> float:
    query_tokens = _tokens(task.query)
    if not query_tokens:
        return 0.0
    content_tokens = _tokens(item.content)
    metadata_tokens = _tokens(" ".join((item.kind, *item.tags, *item.scopes)))
    content_overlap = len(query_tokens & content_tokens)
    metadata_overlap = len(query_tokens & metadata_tokens)
    if content_overlap + metadata_overlap == 0:
        return 0.0
    normalized_query = " ".join(match.casefold() for match in task.query.split())
    normalized_content = " ".join(match.casefold() for match in item.content.split())
    exact_phrase_bonus = 1.0 if normalized_query in normalized_content else 0.0
    score = (
        exact_phrase_bonus
        + (content_overlap / len(query_tokens))
        + (0.25 * metadata_overlap / len(query_tokens))
    )
    return round(score, 12)


def _rank_lexically(
    task: RetrievalTask,
    objects: Sequence[MemoryObject],
) -> tuple[tuple[MemoryObject, float], ...]:
    scored: list[tuple[MemoryObject, float, int]] = []
    for ordinal, item in enumerate(objects):
        score = _lexical_score(task, item)
        if score > 0.0:
            scored.append((item, score, ordinal))
    scored.sort(key=lambda value: (-value[1], -value[2], value[0].object_id))
    return tuple((item, score) for item, score, _ in scored)


class FixedBudgetHistoryBaseline:
    """Recent full-history window under the same fixed context-character cap."""

    manifest = AdapterManifest(
        adapter_id="fixed-budget-long-history",
        name="Fixed-budget authorized history",
        version="1",
    )

    def __init__(self, context_budget_chars: int) -> None:
        self._context_budget_chars = context_budget_chars
        self._objects: tuple[MemoryObject, ...] = ()
        self.benchmark_metadata = BenchmarkMetadata(
            source_representation="authorized_append_history",
            selection_mode="recent_first_fixed_character_window",
            context_budget_chars=context_budget_chars,
            validity_limitations=(
                "retrieval_abi_proxies_reader_context",
                "fixture_sequence_is_history_order",
                "no_answer_model_or_prompt_cache",
            ),
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        self._objects = tuple(objects)
        return PreparationReceipt(storage_bytes=_stored_content_bytes(self._objects))

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        effective_at = _instant(task.evaluated_at)
        eligible = tuple(
            item
            for item in self._objects
            if _eligible_event(item, task, effective_at, current_only=False)
        )
        budget = task.context_budget_chars or self._context_budget_chars
        selected = _budgeted(
            ((item, None) for item in reversed(eligible)),
            limit=task.limit,
            context_budget_chars=budget,
        )
        return RetrievalReceipt(items=selected, abstained=not selected)

    def close(self) -> None:
        self._objects = ()


class StaticProfileBaseline:
    """Frozen hand-selected compact profile with no update resolution."""

    manifest = AdapterManifest(
        adapter_id="static-profile",
        name="Frozen compact static profile",
        version="1",
    )

    def __init__(
        self,
        profile_object_ids: Sequence[str],
        context_budget_chars: int,
    ) -> None:
        self._profile_object_ids = tuple(profile_object_ids)
        self._context_budget_chars = context_budget_chars
        self._profile: tuple[MemoryObject, ...] = ()
        self.benchmark_metadata = BenchmarkMetadata(
            source_representation="frozen_curated_profile",
            selection_mode="profile_order_fixed_character_window",
            context_budget_chars=context_budget_chars,
            validity_limitations=(
                "manual_profile_membership",
                "no_profile_update_resolution",
                "retrieval_abi_proxies_reader_context",
            ),
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        objects_by_id = {item.object_id: item for item in objects}
        missing = set(self._profile_object_ids) - objects_by_id.keys()
        if missing:
            raise ValueError("static profile references objects outside the snapshot")
        self._profile = tuple(objects_by_id[object_id] for object_id in self._profile_object_ids)
        return PreparationReceipt(storage_bytes=_stored_content_bytes(self._profile))

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        eligible = tuple(
            item
            for item in self._profile
            if (not task.scopes or set(task.scopes).intersection(item.scopes))
            and _project_scope_applies(item, task)
        )
        budget = task.context_budget_chars or self._context_budget_chars
        selected = _budgeted(
            ((item, None) for item in eligible),
            limit=task.limit,
            context_budget_chars=budget,
        )
        return RetrievalReceipt(items=selected, abstained=not selected)

    def close(self) -> None:
        self._profile = ()


class RawAppendLogSearchBaseline:
    """Exact/lexical search over raw authorized events without current-state collapse."""

    manifest = AdapterManifest(
        adapter_id="raw-append-log-search",
        name="Raw append-log exact and lexical search",
        version="1",
    )

    def __init__(self, context_budget_chars: int) -> None:
        self._context_budget_chars = context_budget_chars
        self._objects: tuple[MemoryObject, ...] = ()
        self.benchmark_metadata = BenchmarkMetadata(
            source_representation="raw_authorized_append_log",
            selection_mode="exact_phrase_and_token_overlap",
            context_budget_chars=context_budget_chars,
            validity_limitations=(
                "no_supersession_or_expiry_resolution",
                "lexical_only",
                "retrieval_only_no_reader",
            ),
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        self._objects = tuple(objects)
        return PreparationReceipt(storage_bytes=_stored_content_bytes(self._objects))

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        effective_at = _instant(task.evaluated_at)
        eligible = tuple(
            item
            for item in self._objects
            if _eligible_event(item, task, effective_at, current_only=False)
        )
        budget = task.context_budget_chars or self._context_budget_chars
        selected = _budgeted(
            _rank_lexically(task, eligible),
            limit=task.limit,
            context_budget_chars=budget,
        )
        return RetrievalReceipt(items=selected, abstained=not selected)

    def close(self) -> None:
        self._objects = ()


class StableObservationLogBaseline:
    """Lexical stable-log search after deterministic current-state resolution."""

    manifest = AdapterManifest(
        adapter_id="stable-observation-current-state",
        name="Stable observation log with current-state resolution",
        version="1",
    )

    def __init__(self, context_budget_chars: int) -> None:
        self._context_budget_chars = context_budget_chars
        self._objects: tuple[MemoryObject, ...] = ()
        self.benchmark_metadata = BenchmarkMetadata(
            source_representation="stable_observation_log",
            selection_mode="current_state_then_exact_phrase_and_token_overlap",
            context_budget_chars=context_budget_chars,
            validity_limitations=(
                "deterministic_fixture_order",
                "may_be_fixture_aligned",
                "simple_reference_not_implementation_acceptance",
                "lexical_only",
                "no_reflection_or_reader",
            ),
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        self._objects = tuple(objects)
        return PreparationReceipt(storage_bytes=_stored_content_bytes(self._objects))

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        effective_at = _instant(task.evaluated_at)
        historical = tuple(
            item
            for item in self._objects
            if item.valid_from is None or _instant(item.valid_from) <= effective_at
        )
        active = tuple(
            item
            for item in historical
            if item.expires_at is None or _instant(item.expires_at) > effective_at
        )
        superseded = {item.supersedes for item in historical if item.supersedes is not None}
        resolved = tuple(item for item in active if item.object_id not in superseded)
        current = tuple(
            item
            for item in resolved
            if (not task.scopes or set(task.scopes).intersection(item.scopes))
            and _project_scope_applies(item, task)
        )
        budget = task.context_budget_chars or self._context_budget_chars
        selected = _budgeted(
            _rank_lexically(task, current),
            limit=task.limit,
            context_budget_chars=budget,
        )
        return RetrievalReceipt(items=selected, abstained=not selected)

    def close(self) -> None:
        self._objects = ()


class BoundedLocalFileSearchBaseline:
    """Bounded deterministic file materialization and exact/lexical scanning control."""

    manifest = AdapterManifest(
        adapter_id="bounded-local-file-search",
        name="Bounded local file-search control",
        version="1",
    )

    def __init__(
        self,
        work_dir: Path,
        *,
        context_budget_chars: int,
        max_files_scanned: int,
        max_bytes_scanned: int,
    ) -> None:
        self._work_dir = work_dir
        self._context_budget_chars = context_budget_chars
        self._max_files_scanned = max_files_scanned
        self._max_bytes_scanned = max_bytes_scanned
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._files: tuple[tuple[Path, MemoryObject], ...] = ()
        self.benchmark_metadata = BenchmarkMetadata(
            source_representation="local_event_files",
            selection_mode="bounded_file_scan_exact_phrase_and_token_overlap",
            context_budget_chars=context_budget_chars,
            max_files_scanned=max_files_scanned,
            max_bytes_scanned=max_bytes_scanned,
            validity_limitations=(
                "infrastructure_control_only",
                "deterministic_file_ranker_not_action_model",
                "programmatic_log_search_not_exercised",
                "no_supersession_or_expiry_resolution",
            ),
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        if self._temporary is not None:
            raise RuntimeError("file-search baseline may only be prepared once")
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="atc-memory-file-search-",
            dir=self._work_dir,
        )
        root = Path(self._temporary.name)
        files: list[tuple[Path, MemoryObject]] = []
        for ordinal, item in enumerate(objects):
            path = root / f"event-{ordinal:06d}.json"
            payload = {
                "content": item.content,
                "kind": item.kind,
                "scopes": item.scopes,
                "tags": item.tags,
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            files.append((path, item))
        self._files = tuple(files)
        storage_bytes = sum(path.stat().st_size for path, _ in self._files)
        return PreparationReceipt(storage_bytes=storage_bytes)

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        if self._temporary is None:
            raise RuntimeError("file-search baseline must be prepared before retrieval")
        effective_at = _instant(task.evaluated_at)
        scanned: list[MemoryObject] = []
        scanned_bytes = 0
        for file_index, (path, item) in enumerate(self._files, 1):
            if file_index > self._max_files_scanned:
                break
            size = path.stat().st_size
            if scanned_bytes + size > self._max_bytes_scanned:
                break
            rendered = path.read_text(encoding="utf-8")
            scanned_bytes += len(rendered.encode("utf-8"))
            if _eligible_event(item, task, effective_at, current_only=False):
                scanned.append(item)
        budget = task.context_budget_chars or self._context_budget_chars
        selected = _budgeted(
            _rank_lexically(task, scanned),
            limit=task.limit,
            context_budget_chars=budget,
        )
        return RetrievalReceipt(items=selected, abstained=not selected)

    def close(self) -> None:
        self._files = ()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
