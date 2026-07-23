from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from allthecontext.memory_lab import (
    ADAPTER_ABI,
    MEMORY_OBJECT_SCHEMA,
    REPORT_SCHEMA,
    AdapterManifest,
    MemoryObject,
    PreparationReceipt,
    RankedMemory,
    RetrievalReceipt,
    RetrievalTask,
    evaluate_adapter,
)

from bench.memory_lab import FIXTURES, load_fixture, run_fixture

FIXTURE_SHA256 = "5601692ea305448f6b299c32725a93c73ca83ccee66f325e22cbcbedfa0cc68f"


class FutureCompetitorAdapter:
    """Minimal proof that a future adapter needs no ATC storage dependency."""

    manifest = AdapterManifest(
        adapter_id="future-competitor-stub",
        name="Future competitor stub",
        version="test",
    )

    def __init__(self) -> None:
        self.object_ids: frozenset[str] = frozenset()

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        self.object_ids = frozenset(item.object_id for item in objects)
        return PreparationReceipt()

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        object_id = "fabricated-result"
        return RetrievalReceipt(items=(RankedMemory(object_id),), abstained=False)

    def close(self) -> None:
        self.object_ids = frozenset()


def test_memory_lab_fixture_is_frozen_sanitized_and_schema_versioned() -> None:
    objects, tasks = load_fixture()

    assert hashlib.sha256(FIXTURES.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert all(item.schema == MEMORY_OBJECT_SCHEMA for item in objects)
    assert {task.task_id for task in tasks} == {
        "release-brief",
        "current-lodging",
        "meal-plan",
        "project-scope",
        "unknown-topic",
    }
    assert len({item.object_id for item in objects}) == len(objects)


def test_lab_compares_simple_baseline_and_current_atc_without_content_in_report(
    tmp_path: Path,
) -> None:
    report = run_fixture(tmp_path, repeats=2)

    assert report["schema"] == REPORT_SCHEMA
    assert report["adapter_abi"] == ADAPTER_ABI
    assert report["fixture_sha256"] == FIXTURE_SHA256
    assert set(report["adapters"]) == {
        "no-memory",
        "deterministic-token-overlap",
        "atc-retrieval-v3",
    }
    no_memory = report["adapters"]["no-memory"]
    baseline = report["adapters"]["deterministic-token-overlap"]
    atc = report["adapters"]["atc-retrieval-v3"]
    assert no_memory["metrics"]["task_success_rate"] == 0.2
    assert no_memory["metrics"]["mean_evidence_group_recall"] == 0.2
    assert no_memory["preparation"]["storage_bytes"] == 0
    assert baseline["metrics"]["task_success_rate"] == 0.8
    assert baseline["metrics"]["forbidden_output_count"] == 1
    assert atc["metrics"]["task_success_rate"] == 0.8
    assert atc["metrics"]["forbidden_output_count"] == 0
    assert atc["metrics"]["mean_evidence_group_recall"] == 0.9
    for result in (no_memory, baseline, atc):
        assert result["metrics"]["contract_violation_count"] == 0
        assert result["metrics"]["deterministic_task_rate"] == 1.0
        assert result["metrics"]["abstention_accuracy"] == 1.0
        assert result["manifest"]["writes_canonical_state"] is False
        assert result["manifest"]["network_access"] is False

    rendered = json.dumps(report, sort_keys=True)
    for item in load_fixture()[0]:
        assert item.content not in rendered
        assert item.object_id not in rendered
    for task in load_fixture()[1]:
        assert task.task_id not in rendered
        assert task.query not in rendered


def test_future_adapter_contract_surfaces_unknown_result_ids() -> None:
    objects = (MemoryObject("known", "fact", "Known synthetic fact."),)
    tasks = (
        RetrievalTask(
            task_id="known-task",
            query="known",
            evaluated_at="2026-01-01T00:00:00+00:00",
            limit=2,
            evidence_groups=(frozenset({"known"}),),
        ),
    )

    report = evaluate_adapter(FutureCompetitorAdapter(), objects, tasks, repeats=1)

    assert report["metrics"]["contract_violation_count"] == 1
    assert report["metrics"]["task_success_rate"] == 0.0
    assert report["tasks"][0]["returned_count"] == 1
    assert report["tasks"][0]["ranking_fingerprint"]
    assert "fabricated-result" not in json.dumps(report)


def test_adapter_manifest_fails_closed_on_authority_or_egress_misdeclaration() -> None:
    with pytest.raises(ValueError, match="canonical"):
        AdapterManifest("writer", "Writer", "1", writes_canonical_state=True)
    with pytest.raises(ValueError, match="egress"):
        AdapterManifest("egress", "Egress", "1", data_egress=("memory content",))
