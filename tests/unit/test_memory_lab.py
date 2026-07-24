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
from allthecontext.memory_lab_baselines import (
    RawAppendLogSearchBaseline,
    StableObservationLogBaseline,
)

from bench.memory_lab import (
    FIXTURES,
    LADDER_CONFIG,
    load_fixture,
    load_fixture_bundle,
    run_fixture,
)

FIXTURE_SHA256 = "5601692ea305448f6b299c32725a93c73ca83ccee66f325e22cbcbedfa0cc68f"
LADDER_CONFIG_SHA256 = "6dbf75db008b1be2d3db643b8dd19fe45f1a45c88121ac1ac3af16a0a0cd3c98"


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


class BudgetEscapeAdapter:
    manifest = AdapterManifest(
        adapter_id="budget-escape-stub",
        name="Budget escape stub",
        version="test",
    )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        _ = objects
        return PreparationReceipt()

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        _ = task
        return RetrievalReceipt(items=(RankedMemory("known"),), abstained=False)

    def close(self) -> None:
        return None


def test_memory_lab_fixture_is_frozen_sanitized_and_schema_versioned() -> None:
    objects, tasks = load_fixture()

    assert hashlib.sha256(FIXTURES.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert hashlib.sha256(LADDER_CONFIG.read_bytes()).hexdigest() == LADDER_CONFIG_SHA256
    assert all(item.schema == MEMORY_OBJECT_SCHEMA for item in objects)
    assert {task.task_id for task in tasks} == {
        "release-brief",
        "current-lodging",
        "meal-plan",
        "project-scope",
        "unknown-topic",
    }
    assert len({item.object_id for item in objects}) == len(objects)
    assert {task.context_budget_chars for task in tasks} == {None}
    assert {task.context_budget_chars for task in load_fixture_bundle()[1]} == {260}


def test_lab_compares_simple_baseline_and_current_atc_without_content_in_report(
    tmp_path: Path,
) -> None:
    report = run_fixture(tmp_path, repeats=2)

    assert report["schema"] == REPORT_SCHEMA
    assert report["adapter_abi"] == ADAPTER_ABI
    assert report["fixture_sha256"] == FIXTURE_SHA256
    assert report["baseline_config_sha256"] == LADDER_CONFIG_SHA256
    assert tuple(report["adapters"]) == (
        "no-memory",
        "fixed-budget-long-history",
        "static-profile",
        "raw-append-log-search",
        "stable-observation-current-state",
        "bounded-local-file-search",
        "atc-retrieval-v3",
    )
    no_memory = report["adapters"]["no-memory"]
    long_history = report["adapters"]["fixed-budget-long-history"]
    static_profile = report["adapters"]["static-profile"]
    append_log = report["adapters"]["raw-append-log-search"]
    stable_log = report["adapters"]["stable-observation-current-state"]
    file_search = report["adapters"]["bounded-local-file-search"]
    atc = report["adapters"]["atc-retrieval-v3"]
    assert no_memory["metrics"]["task_success_rate"] == 0.2
    assert no_memory["metrics"]["mean_evidence_group_recall"] == 0.2
    assert no_memory["preparation"]["storage_bytes"] == 0
    assert long_history["metrics"]["task_success_rate"] == 0.4
    assert static_profile["metrics"]["task_success_rate"] == 0.6
    assert append_log["metrics"]["task_success_rate"] == 0.8
    assert append_log["metrics"]["forbidden_output_count"] == 1
    assert stable_log["metrics"]["task_success_rate"] == 1.0
    assert stable_log["metrics"]["forbidden_output_count"] == 0
    assert file_search["metrics"]["task_success_rate"] == 0.8
    assert file_search["metrics"]["forbidden_output_count"] == 1
    assert (
        "programmatic_log_search_not_exercised" in file_search["benchmark"]["validity_limitations"]
    )
    assert atc["metrics"]["task_success_rate"] == 0.8
    assert atc["metrics"]["forbidden_output_count"] == 0
    assert atc["metrics"]["mean_evidence_group_recall"] == 0.9
    for result in report["adapters"].values():
        assert result["metrics"]["contract_violation_count"] == 0
        assert result["metrics"]["budget_violation_count"] == 0
        assert result["metrics"]["deterministic_task_rate"] == 1.0
        assert result["manifest"]["writes_canonical_state"] is False
        assert result["manifest"]["network_access"] is False
        assert result["metrics"]["usage"]["monetary_cost_usd"] == 0.0
        assert all(task["disclosure_chars"] <= 260 for task in result["tasks"])

    assessments = report["baseline_ladder"]["rungs"]
    assert assessments["no-memory"]["decision"] == "retain_control"
    assert assessments["stable-observation-current-state"]["decision"] == "advance_to_next_fixture"
    assert assessments["atc-retrieval-v3"]["decision"] == "not_earned_on_this_fixture"

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

    assert report["manifest"]["abi"] == "atc.memory-lab.retrieval-adapter.v1"
    assert report["metrics"]["contract_violation_count"] == 1
    assert report["metrics"]["task_success_rate"] == 0.0
    assert report["tasks"][0]["returned_count"] == 1
    assert report["tasks"][0]["ranking_fingerprint"]
    assert "fabricated-result" not in json.dumps(report)


def test_v1_adapter_remains_compatible_with_optional_task_budget() -> None:
    objects = (MemoryObject("known", "fact", "Known synthetic fact."),)
    task = RetrievalTask(
        task_id="known-task",
        query="known",
        evaluated_at="2026-01-01T00:00:00+00:00",
        limit=2,
        evidence_groups=(frozenset({"known"}),),
        context_budget_chars=8,
    )

    report = evaluate_adapter(FutureCompetitorAdapter(), objects, (task,), repeats=1)

    assert ADAPTER_ABI == "atc.memory-lab.retrieval-adapter.v1"
    assert report["manifest"]["abi"] == ADAPTER_ABI
    assert report["metrics"]["budget_violation_count"] == 0


def test_optional_task_budget_is_reported_without_changing_the_v1_result_abi() -> None:
    objects = (MemoryObject("known", "fact", "Known synthetic fact."),)
    task = RetrievalTask(
        task_id="known-task",
        query="known",
        evaluated_at="2026-01-01T00:00:00+00:00",
        limit=2,
        evidence_groups=(frozenset({"known"}),),
        context_budget_chars=8,
    )

    report = evaluate_adapter(BudgetEscapeAdapter(), objects, (task,), repeats=1)

    assert report["manifest"]["abi"] == ADAPTER_ABI
    assert report["metrics"]["budget_violation_count"] == 1
    assert report["metrics"]["task_success_rate"] == 0.0
    assert report["failure_cases"] == [
        {"task_index": 0, "reason_codes": ["context_budget_exceeded"]}
    ]


def test_raw_log_surfaces_stale_event_while_stable_log_resolves_current_state() -> None:
    objects, tasks, config = load_fixture_bundle()
    task = tasks[1]
    raw = RawAppendLogSearchBaseline(config.context_budget_chars)
    stable = StableObservationLogBaseline(config.context_budget_chars)

    raw.prepare(objects)
    stable.prepare(objects)
    try:
        raw_ids = {item.object_id for item in raw.retrieve(task).items}
        stable_ids = {item.object_id for item in stable.retrieve(task).items}
    finally:
        raw.close()
        stable.close()

    assert "summit-hotel-old" in raw_ids
    assert "summit-hotel-current" in raw_ids
    assert stable_ids == {"summit-hotel-current"}


def test_stable_log_does_not_resurrect_superseded_state_after_replacement_expires() -> None:
    objects = (
        MemoryObject(
            "old",
            "fact",
            "Synthetic color is amber.",
            valid_from="2026-01-01T00:00:00+00:00",
        ),
        MemoryObject(
            "replacement",
            "fact",
            "Synthetic color is violet.",
            valid_from="2026-02-01T00:00:00+00:00",
            expires_at="2026-03-01T00:00:00+00:00",
            supersedes="old",
        ),
    )
    task = RetrievalTask(
        task_id="color",
        query="synthetic color",
        evaluated_at="2026-04-01T00:00:00+00:00",
        limit=2,
        context_budget_chars=100,
    )
    stable = StableObservationLogBaseline(100)

    stable.prepare(objects)
    try:
        receipt = stable.retrieve(task)
    finally:
        stable.close()

    assert receipt.abstained
    assert receipt.items == ()


def test_stable_log_resolves_supersession_before_cross_scope_applicability() -> None:
    objects = (
        MemoryObject(
            "broad-old",
            "fact",
            "Synthetic route uses amber.",
            scopes=("project:atlas",),
            valid_from="2026-01-01T00:00:00+00:00",
        ),
        MemoryObject(
            "narrow-replacement",
            "fact",
            "Synthetic route uses violet.",
            scopes=("project:orchid",),
            valid_from="2026-02-01T00:00:00+00:00",
            supersedes="broad-old",
        ),
    )
    task = RetrievalTask(
        task_id="route",
        query="synthetic route",
        evaluated_at="2026-04-01T00:00:00+00:00",
        limit=2,
        scopes=("project:atlas",),
        current_project="atlas",
        context_budget_chars=100,
    )
    stable = StableObservationLogBaseline(100)

    stable.prepare(objects)
    try:
        receipt = stable.retrieve(task)
    finally:
        stable.close()

    assert receipt.abstained
    assert receipt.items == ()


def test_adapter_manifest_fails_closed_on_authority_or_egress_misdeclaration() -> None:
    with pytest.raises(ValueError, match="canonical"):
        AdapterManifest("writer", "Writer", "1", writes_canonical_state=True)
    with pytest.raises(ValueError, match="egress"):
        AdapterManifest("egress", "Egress", "1", data_egress=("memory content",))
