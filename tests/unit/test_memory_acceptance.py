from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from bench.cross_client_reader_outcomes import ReaderUsage
from bench.memory_acceptance import (
    ROOT,
    Chunk,
    Citation,
    Event,
    Fact,
    Observation,
    OperationReceipt,
    Oracle,
    Request,
    digest,
    grade,
    load_fixture,
    run_fixture,
)
from bench.memory_acceptance_atc import AtcAdapter

DEVELOPMENT_SHA = "ae3d9b8b8a3fdb39e61d5d1571e5e617ceff28e99ab11b092e1f8da7d9d18dce"
HOLDOUT_SHA = "838811f7529eb53b889652a690a1227c1549b52f3b54fd9af807cc7309f7ae7b"


def test_version_one_freeze_and_coverage() -> None:
    fixture, sha = load_fixture("development")
    assert sha == DEVELOPMENT_SHA
    assert len(fixture.scenarios) == 6
    assert {case.audience for case in fixture.scenarios} == {"personal", "engineering"}
    capabilities = {item for case in fixture.scenarios for item in case.capabilities}
    assert {
        "mixed_statements",
        "missed_decision",
        "correction",
        "supersession",
        "partial_recall",
        "unknown",
        "provenance",
        "project_isolation",
        "restart",
        "rebuild",
        "deletion",
        "replay",
        "cross_app_delivery",
        "capture_to_delivery",
    } <= capabilities
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["partitions"]["development"]["sha256"] == DEVELOPMENT_SHA
    assert manifest["partitions"]["holdout"]["sha256"] == HOLDOUT_SHA
    # Only integrity is checked: held-out cases are never executed or graded here.
    raw = json.loads((ROOT / "holdout" / "scenarios.json").read_text(encoding="utf-8"))
    assert digest(raw) == HOLDOUT_SHA


def test_holdout_is_not_a_development_default() -> None:
    with pytest.raises(ValueError, match="frozen candidate identity"):
        load_fixture("holdout")
    with pytest.raises(ValueError, match="unknown fixture partition"):
        load_fixture("all")


@pytest.fixture
def grading_input() -> tuple[Request, Oracle, dict[str, Event], Chunk]:
    current = Event(
        event_id="CURRENT",
        at="2026-09-01T10:00:00Z",
        app="codex",
        project="Borealis",
        role="user",
        text="Borealis launches in June.",
    )
    old = current.model_copy(update={"event_id": "OLD", "text": "Borealis launches in May."})
    other = current.model_copy(
        update={"event_id": "OTHER", "project": "Cedar", "text": "Cedar launches in July."}
    )
    request = Request(
        checkpoint_id="PROBE",
        question="When does Borealis launch?",
        project="Borealis",
        surface="core_api",
    )
    oracle = Oracle(
        facts=(Fact(fact_id="current-date", pattern="June", sources=("CURRENT",)),),
        forbidden_patterns=("launches in May",),
    )
    chunk = Chunk(
        text=current.text, citations=(Citation(source_event_id="CURRENT", quote=current.text),)
    )
    return request, oracle, {item.event_id: item for item in (current, old, other)}, chunk


def test_supported_current_evidence_passes(grading_input: tuple) -> None:
    request, oracle, events, chunk = grading_input
    assert grade(request, oracle, Observation(retrieved=(chunk,), delivered=(chunk,)), events) == []


def test_capture_or_retrieval_does_not_imply_delivery(grading_input: tuple) -> None:
    request, oracle, events, chunk = grading_input
    failures = grade(request, oracle, Observation(retrieved=(chunk,)), events)
    assert {(item["stage"], item["code"]) for item in failures} == {("compile", "MISSING_FACT")}


@pytest.mark.parametrize(
    "source,quote",
    [("OLD", "Borealis launches in May."), ("CURRENT", "invented quote"), ("NONEXISTENT", "June")],
)
def test_current_answer_with_stale_or_invented_citation_fails(
    grading_input: tuple, source: str, quote: str
) -> None:
    request, oracle, events, chunk = grading_input
    wrong = chunk.model_copy(update={"citations": (Citation(source_event_id=source, quote=quote),)})
    failures = grade(request, oracle, Observation(retrieved=(wrong,), delivered=(wrong,)), events)
    assert "MISSING_CURRENT_PROVENANCE" in {item["code"] for item in failures}


def test_foreign_project_and_stale_content_fail_independently(grading_input: tuple) -> None:
    request, oracle, events, chunk = grading_input
    foreign = Chunk(
        text="Cedar launches in July.",
        citations=(Citation(source_event_id="OTHER", quote="Cedar launches in July."),),
    )
    stale = Chunk(text="Borealis launches in May.")
    failures = grade(
        request,
        oracle,
        Observation(retrieved=(chunk, foreign, stale), delivered=(chunk, foreign, stale)),
        events,
    )
    assert {"CROSS_PROJECT_SOURCE", "FORBIDDEN_INFLUENCE"} <= {item["code"] for item in failures}


def test_stale_line_is_detected_inside_hook_frame(grading_input: tuple) -> None:
    request, oracle, events, chunk = grading_input
    framed = Chunk(text="Reference context:\nBorealis launches in May.\n")
    exact = oracle.model_copy(update={"forbidden_patterns": ("^Borealis launches in May\\.$",)})
    failures = grade(request, exact, Observation(retrieved=(chunk,), delivered=(framed,)), events)
    assert any(
        item["stage"] == "compile" and item["code"] == "FORBIDDEN_INFLUENCE" for item in failures
    )


def test_partial_knowledge_and_unknown_are_separate(grading_input: tuple) -> None:
    request, oracle, events, chunk = grading_input
    partial = oracle.model_copy(update={"unknowns": ("rollback_procedure",)})
    observation = Observation(retrieved=(chunk,), delivered=(chunk,))
    failures = grade(request, partial, observation, events)
    assert {item["code"] for item in failures} == {"UNKNOWN_NOT_SIGNALLED"}
    assert (
        grade(
            request,
            partial,
            observation.model_copy(update={"unknowns": ("rollback_procedure",)}),
            events,
        )
        == []
    )


class BoundarySpy:
    adapter_id = "test-only-boundary-spy-not-a-memory-engine"
    seen: ClassVar[list[dict]] = []

    def reset(self, root: Path, scenario_id: str) -> None:
        pass

    def capture(self, event: Event) -> OperationReceipt:
        self.seen.append(event.model_dump())
        return OperationReceipt(status="ok")

    def control(self, operation: str, event_id: str | None) -> OperationReceipt:
        return OperationReceipt(status="unsupported")

    def checkpoint(self, request: Request) -> Observation:
        self.seen.append(request.model_dump())
        return Observation()

    def usage(self) -> ReaderUsage:
        return ReaderUsage()

    def close(self) -> None:
        pass


def test_adapter_boundary_never_receives_gold_and_unsupported_is_not_success(
    tmp_path: Path,
) -> None:
    BoundarySpy.seen = []
    report = run_fixture(BoundarySpy, tmp_path, candidate_identity="test-only")
    assert report["summary"]["accepted"] is False
    assert report["summary"]["operation_failures"] > 0
    assert all(
        {"expect", "facts", "sources", "unknowns", "forbidden_patterns"}.isdisjoint(value)
        for value in BoundarySpy.seen
    )
    assert report["partition"] == "development"
    assert report["evidence"]["installed_clients"] == "UNRUN"


def test_atc_baseline_is_deterministic_without_weakening_expectations(tmp_path: Path) -> None:
    first = run_fixture(AtcAdapter, tmp_path / "first", candidate_identity="source-under-test")
    second = run_fixture(AtcAdapter, tmp_path / "second", candidate_identity="source-under-test")
    assert first == second
    # Deliberately no assertion of a favorable pass count: production repairs
    # should improve the evaluation without changing this test or the oracle.
    assert first["summary"]["checkpoints"] == 14
    assert all(
        item["status"] in {"ok", "replayed"}
        for item in first["operations"]
        if item["operation"] == "capture"
    )
    assert all(
        item["model_calls"] == item["provider_calls"] == item["network_calls"] == 0
        for item in first["usage_by_scenario"]
    )
