"""Optional Hindsight supplier boundary for the ATC Memory Lab.

This module imports no Hindsight package.  A disposable experiment may inject
an official Hindsight client only after independently creating and reviewing an
isolated runtime.  The adapter is deliberately limited to the zero-provider-
egress chunk-store declaration and never treats Hindsight state as canonical.
Transport isolation must be enforced outside this process.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from allthecontext.memory_lab import (
    AdapterManifest,
    AdapterUsage,
    MemoryObject,
    PreparationReceipt,
    RankedMemory,
    RetrievalReceipt,
    RetrievalTask,
)

HINDSIGHT_API_VERSION = "0.8.5"
HINDSIGHT_SOURCE_REVISION = "fa69b5b73b3b50bf5dcbae5bccbc7197de03692f"
_PIN_RE = re.compile(r"[0-9a-f]{40}")
_BANK_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class HindsightClient(Protocol):
    """Subset of the official 0.8.5 client used by the lab adapter."""

    def retain(
        self,
        bank_id: str,
        content: str,
        *,
        timestamp: datetime | None = None,
        context: str | None = None,
        document_id: str | None = None,
        metadata: dict[str, str] | None = None,
        tags: list[str] | None = None,
        retain_async: bool = False,
    ) -> object: ...

    def recall(
        self,
        bank_id: str,
        query: str,
        *,
        max_tokens: int,
        budget: str,
        trace: bool,
        query_timestamp: str | None,
        include_entities: bool,
        include_chunks: bool,
        include_source_facts: bool,
        tags: list[str] | None,
        tags_match: str,
    ) -> object: ...

    def delete_bank(self, bank_id: str) -> object: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class HindsightRuntimeDeclaration:
    """Fail-closed declaration for a reviewed, externally isolated runtime."""

    source_revision: str = HINDSIGHT_SOURCE_REVISION
    api_version: str = HINDSIGHT_API_VERSION
    llm_provider: str = "none"
    embeddings_provider: str = "local"
    embeddings_model: str = ""
    embeddings_model_revision: str = ""
    reranker_provider: str = "rrf"
    network_access: bool = True
    data_egress: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_revision != HINDSIGHT_SOURCE_REVISION:
            raise ValueError("Hindsight source must match the reviewed commit")
        if self.api_version != HINDSIGHT_API_VERSION:
            raise ValueError("Hindsight API must match the reviewed version")
        if self.llm_provider != "none":
            raise ValueError("Hindsight supplier runs must disable provider-backed LLM use")
        if self.embeddings_provider not in {"local", "onnx"}:
            raise ValueError("Hindsight embeddings must use a reviewed local provider")
        if not self.embeddings_model.strip():
            raise ValueError("Hindsight embeddings require an explicit model identifier")
        if not _PIN_RE.fullmatch(self.embeddings_model_revision):
            raise ValueError("Hindsight embeddings require an exact 40-hex source revision")
        if self.reranker_provider != "rrf":
            raise ValueError("Hindsight supplier runs must use the non-neural RRF reranker")
        if self.data_egress:
            raise ValueError("Hindsight supplier runs cannot declare provider data egress")


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _scope_tags(scopes: Sequence[str]) -> list[str]:
    return [f"atc-scope:{scope}" for scope in scopes]


def _usage_is_zero(response: object) -> bool:
    usage = _field(response, "usage")
    if usage is None:
        return True
    return not any(
        int(_field(usage, name, 0) or 0)
        for name in ("input_tokens", "output_tokens", "total_tokens", "thoughts_tokens")
    )


class HindsightRetrievalAdapter:
    """Translate the retrieval-only Memory Lab ABI to an injected client.

    The supplier receives only the already-authorized snapshot.  ATC object IDs
    are carried as Hindsight ``document_id`` values and are the only identifiers
    returned to the harness.  Hindsight content and internal fact IDs never
    cross the result ABI.
    """

    def __init__(
        self,
        client: HindsightClient,
        *,
        bank_id: str,
        runtime: HindsightRuntimeDeclaration,
        storage_bytes: Callable[[], int],
        recall_max_tokens: int = 2_048,
    ) -> None:
        if not _BANK_RE.fullmatch(bank_id):
            raise ValueError("bank_id must be a bounded opaque identifier")
        if recall_max_tokens < 1:
            raise ValueError("recall_max_tokens must be positive")
        self._client = client
        self._bank_id = bank_id
        self._runtime = runtime
        self._storage_bytes = storage_bytes
        self._recall_max_tokens = recall_max_tokens
        self._known_ids: frozenset[str] = frozenset()
        self._prepared = False
        self._closed = False
        provider = (
            f"hindsight/{runtime.api_version};llm={runtime.llm_provider};"
            f"embeddings={runtime.embeddings_provider}:"
            f"{runtime.embeddings_model}@{runtime.embeddings_model_revision};"
            f"reranker={runtime.reranker_provider}"
        )
        self.manifest = AdapterManifest(
            adapter_id="competitor-hindsight",
            name="Hindsight supplier adapter",
            version=runtime.source_revision,
            provider=provider,
            network_access=runtime.network_access,
            data_egress=runtime.data_egress,
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        if self._prepared or self._closed:
            raise RuntimeError("Hindsight adapter may only be prepared once")
        known_ids = frozenset(item.object_id for item in objects)
        if len(known_ids) != len(objects):
            raise ValueError("Hindsight adapter requires unique object ids")
        self._known_ids = known_ids
        try:
            for item in objects:
                response = self._client.retain(
                    self._bank_id,
                    item.content,
                    timestamp=_timestamp(item.valid_from),
                    context=None,
                    document_id=item.object_id,
                    metadata={
                        "atc_object_id": item.object_id,
                        "atc_schema": item.schema,
                    },
                    tags=_scope_tags(item.scopes),
                    retain_async=False,
                )
                if _field(response, "success", True) is not True:
                    raise RuntimeError("Hindsight retain did not report success")
                if not _usage_is_zero(response):
                    raise RuntimeError(
                        "Hindsight reported model usage despite the none-LLM declaration"
                    )
            measured_storage = self._storage_bytes()
            if measured_storage < 0:
                raise ValueError("storage measurement must be non-negative")
        except Exception:
            self._delete_bank()
            raise
        self._prepared = True
        return PreparationReceipt(
            storage_bytes=measured_storage,
            usage=AdapterUsage(),
        )

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        if not self._prepared or self._closed:
            raise RuntimeError("Hindsight adapter must be prepared before retrieval")
        task_tags = _scope_tags(task.scopes)
        if task.current_project is not None:
            project_tag = f"atc-scope:project:{task.current_project}"
            if project_tag not in task_tags:
                task_tags.append(project_tag)
        response = self._client.recall(
            self._bank_id,
            task.query,
            max_tokens=self._recall_max_tokens,
            budget="mid",
            trace=False,
            query_timestamp=task.evaluated_at,
            include_entities=False,
            include_chunks=False,
            include_source_facts=False,
            tags=task_tags or None,
            tags_match="any_strict" if task_tags else "any",
        )
        if not _usage_is_zero(response):
            raise RuntimeError(
                "Hindsight reported model usage despite the none-LLM declaration"
            )
        results = _field(response, "results", ())
        ranked: list[RankedMemory] = []
        seen: set[str] = set()
        for ordinal, result in enumerate(results or ()):
            object_id = _field(result, "document_id")
            if not isinstance(object_id, str) or object_id not in self._known_ids:
                object_id = f"__hindsight_unmapped_{ordinal:06d}"
                while object_id in self._known_ids:
                    object_id = f"_{object_id}"
            if object_id in seen:
                continue
            seen.add(object_id)
            scores = _field(result, "scores")
            score = _field(scores, "final") if scores is not None else None
            if score is not None:
                score = float(score)
                if not math.isfinite(score):
                    score = None
            ranked.append(RankedMemory(object_id, score))
            if len(ranked) >= task.limit:
                break
        items = tuple(ranked)
        return RetrievalReceipt(items=items, abstained=not items)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._delete_bank()
        finally:
            self._client.close()
            self._known_ids = frozenset()
            self._closed = True

    def _delete_bank(self) -> None:
        self._client.delete_bank(self._bank_id)
