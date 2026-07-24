from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from allthecontext.memory_lab import MemoryObject, RetrievalTask

from bench.hindsight_supplier_adapter import (
    HINDSIGHT_SOURCE_REVISION,
    HindsightRetrievalAdapter,
    HindsightRuntimeDeclaration,
)

MODEL_REVISION = "a" * 40
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    thoughts_tokens: int = 0


@dataclass
class FakeResponse:
    success: bool = True
    usage: FakeUsage | None = None
    results: tuple[dict[str, Any], ...] = ()


class FakeHindsightClient:
    def __init__(self) -> None:
        self.retains: list[dict[str, Any]] = []
        self.recalls: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.closed = False
        self.results: tuple[dict[str, Any], ...] = ()

    def retain(
        self,
        bank_id: str,
        content: str,
        **kwargs: Any,
    ) -> FakeResponse:
        self.retains.append({"bank_id": bank_id, "content": content, **kwargs})
        return FakeResponse(usage=FakeUsage())

    def recall(self, bank_id: str, query: str, **kwargs: Any) -> FakeResponse:
        self.recalls.append({"bank_id": bank_id, "query": query, **kwargs})
        return FakeResponse(results=self.results)

    def delete_bank(self, bank_id: str) -> None:
        self.deleted.append(bank_id)

    def close(self) -> None:
        self.closed = True


def _runtime(**overrides: Any) -> HindsightRuntimeDeclaration:
    values = {
        "embeddings_model": "synthetic/reviewed-embedding",
        "embeddings_model_revision": MODEL_REVISION,
        "network_access": False,
    }
    values.update(overrides)
    return HindsightRuntimeDeclaration(**values)


def test_hindsight_boundary_is_pinned_zero_egress_and_id_only() -> None:
    client = FakeHindsightClient()
    adapter = HindsightRetrievalAdapter(
        client,
        bank_id="fixture-run-001",
        runtime=_runtime(),
        storage_bytes=lambda: 321,
    )
    objects = (
        MemoryObject(
            "atlas-runbook",
            "workflow",
            "SENSITIVE_SYNTHETIC_MARKER",
            scopes=("project:atlas",),
            valid_from="2026-01-01T00:00:00+00:00",
        ),
        MemoryObject(
            "concise-status",
            "preference",
            "Use concise status.",
            scopes=("preferences",),
        ),
    )

    preparation = adapter.prepare(objects)
    assert preparation.storage_bytes == 321
    assert preparation.usage.model_calls == 0
    assert adapter.manifest.version == HINDSIGHT_SOURCE_REVISION
    assert adapter.manifest.network_access is False
    assert adapter.manifest.data_egress == ()
    assert adapter.manifest.writes_canonical_state is False
    assert client.retains[0]["document_id"] == "atlas-runbook"
    assert client.retains[0]["tags"] == ["atc-scope:project:atlas"]
    assert client.retains[0]["retain_async"] is False

    client.results = (
        {
            "id": "supplier-fact-1",
            "document_id": "atlas-runbook",
            "text": "SENSITIVE_SYNTHETIC_MARKER",
            "scores": {"final": 0.9},
        },
        {
            "id": "supplier-fact-2",
            "document_id": "atlas-runbook",
            "text": "duplicate supplier fact",
            "scores": {"final": 0.8},
        },
        {
            "id": "supplier-fact-3",
            "document_id": None,
            "text": "unmapped supplier fact",
            "scores": {"final": 0.7},
        },
    )
    task = RetrievalTask(
        task_id="release-brief",
        query="Atlas release",
        evaluated_at="2026-06-01T12:00:00+00:00",
        limit=2,
        evidence_groups=(frozenset({"atlas-runbook"}),),
        scopes=("project:atlas",),
        current_project="atlas",
    )

    receipt = adapter.retrieve(task)

    assert [item.object_id for item in receipt.items] == [
        "atlas-runbook",
        "__hindsight_unmapped_000002",
    ]
    assert all("SENSITIVE_SYNTHETIC_MARKER" not in repr(item) for item in receipt.items)
    assert client.recalls == [
        {
            "bank_id": "fixture-run-001",
            "query": "Atlas release",
            "max_tokens": 2048,
            "budget": "mid",
            "trace": False,
            "query_timestamp": "2026-06-01T12:00:00+00:00",
            "include_entities": False,
            "include_chunks": False,
            "include_source_facts": False,
            "tags": ["atc-scope:project:atlas"],
            "tags_match": "any_strict",
        }
    ]

    adapter.close()
    assert client.deleted == ["fixture-run-001"]
    assert client.closed is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"source_revision": "b" * 40}, "reviewed commit"),
        ({"llm_provider": "openai"}, "disable provider-backed"),
        ({"embeddings_provider": "openai"}, "reviewed local"),
        ({"embeddings_model_revision": "main"}, "exact 40-hex"),
        ({"reranker_provider": "local"}, "non-neural RRF"),
        ({"data_egress": ("memory content",)}, "data egress"),
    ],
)
def test_hindsight_runtime_declaration_fails_closed(
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _runtime(**overrides)


def test_hindsight_boundary_rejects_unexpected_model_usage_and_cleans_bank() -> None:
    client = FakeHindsightClient()

    def retain_with_usage(
        bank_id: str,
        content: str,
        **kwargs: Any,
    ) -> FakeResponse:
        _ = (bank_id, content, kwargs)
        return FakeResponse(usage=FakeUsage(input_tokens=1, total_tokens=1))

    client.retain = retain_with_usage  # type: ignore[method-assign]
    adapter = HindsightRetrievalAdapter(
        client,
        bank_id="fixture-run-usage",
        runtime=_runtime(),
        storage_bytes=lambda: 0,
    )

    with pytest.raises(RuntimeError, match="model usage"):
        adapter.prepare((MemoryObject("known", "fact", "Known synthetic fact."),))

    assert client.deleted == ["fixture-run-usage"]


def test_hindsight_boundary_rejects_unexpected_recall_model_usage() -> None:
    client = FakeHindsightClient()
    adapter = HindsightRetrievalAdapter(
        client,
        bank_id="fixture-run-recall-usage",
        runtime=_runtime(),
        storage_bytes=lambda: 0,
    )
    adapter.prepare((MemoryObject("known", "fact", "Known synthetic fact."),))

    def recall_with_usage(
        bank_id: str,
        query: str,
        **kwargs: Any,
    ) -> FakeResponse:
        _ = (bank_id, query, kwargs)
        return FakeResponse(usage=FakeUsage(output_tokens=1, total_tokens=1))

    client.recall = recall_with_usage  # type: ignore[method-assign]

    task = RetrievalTask(
        task_id="unexpected-usage",
        query="Known fact",
        evaluated_at="2026-06-01T12:00:00+00:00",
        limit=1,
        evidence_groups=(frozenset({"known"}),),
    )
    with pytest.raises(RuntimeError, match="model usage"):
        adapter.retrieve(task)


def test_hindsight_receipt_is_an_honest_skip_without_supplier_score() -> None:
    research = REPOSITORY_ROOT / "research" / "memory-lab" / "hindsight"
    provenance = json.loads((research / "provenance.v1.json").read_text(encoding="utf-8"))
    receipt = json.loads((research / "experiment-receipt.v1.json").read_text(encoding="utf-8"))

    assert provenance["official_repository"]["reviewed_revision"] == (HINDSIGHT_SOURCE_REVISION)
    assert provenance["license_review"]["root_spdx"] == "MIT"
    assert provenance["source_cache"]["source_executed"] is False
    assert receipt["status"] == "not_executed_dependency_and_egress_gate"
    assert receipt["benchmark_result"] is None
    assert receipt["attempted_execution"] is False
    assert receipt["provider_calls"] == 0
    assert receipt["fixture_egress"] == 0
    assert receipt["downloads"]["python_packages"] == []
    assert receipt["downloads"]["models"] == []
    assert receipt["future_runtime_prerequisites"]["external_deny_egress"] is True
    assert not any(receipt["prohibited_actions"].values())
