"""Thin natural-language acceptance boundary for the existing Memory Lab.

No model, listener, installed client, or competitor is started by this runner.
Adapters receive events and questions, never evaluator expectations.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from allthecontext.memory_reliability_lab import FailureAttribution
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bench.cross_client_reader_outcomes import ReaderUsage

ABI = "atc.memory-acceptance.adapter.v1"
SCHEMA = "atc.memory-acceptance.fixture.v1"
ROOT = Path(__file__).parent / "memory_acceptance_v1"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Event(FrozenModel):
    event_id: str
    at: str
    app: Literal["codex", "claude_code"]
    project: str | None = None
    role: Literal["user", "assistant", "tool", "imported"]
    text: str = Field(min_length=1, max_length=4000)


class Request(FrozenModel):
    checkpoint_id: str
    question: str
    project: str | None = None
    surface: Literal["core_api", "codex_hook", "claude_code_hook"]
    budget_chars: int = Field(default=4000, ge=256, le=8000)


class Citation(FrozenModel):
    source_event_id: str
    quote: str


class Chunk(FrozenModel):
    text: str
    citations: tuple[Citation, ...] = ()


class Observation(FrozenModel):
    retrieved: tuple[Chunk, ...] = ()
    delivered: tuple[Chunk, ...] = ()
    unknowns: tuple[str, ...] = ()
    supported: bool = True
    detail: str = ""


class OperationReceipt(FrozenModel):
    status: Literal["ok", "replayed", "unsupported", "error"]
    detail: str = ""
    formed_records: int | None = Field(default=None, ge=0)


class MemoryAdapter(Protocol):
    adapter_id: str

    def reset(self, root: Path, scenario_id: str) -> None: ...
    def capture(self, event: Event) -> OperationReceipt: ...
    def control(self, operation: str, event_id: str | None) -> OperationReceipt: ...
    def checkpoint(self, request: Request) -> Observation: ...
    def usage(self) -> ReaderUsage: ...
    def close(self) -> None: ...


class Fact(FrozenModel):
    fact_id: str
    pattern: str
    sources: tuple[str, ...]


class Oracle(FrozenModel):
    facts: tuple[Fact, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()


class Step(FrozenModel):
    event: Event | None = None
    control: Literal["restart", "rebuild", "forget_event", "replay_event"] | None = None
    event_id: str | None = None
    request: Request | None = None
    expect: Oracle | None = None

    @model_validator(mode="after")
    def one_operation(self) -> Step:
        if sum(value is not None for value in (self.event, self.control, self.request)) != 1:
            raise ValueError("a step must contain exactly one operation")
        if (self.request is not None) != (self.expect is not None):
            raise ValueError("only a checkpoint has an evaluator oracle")
        if self.control in {"forget_event", "replay_event"} and self.event_id is None:
            raise ValueError("event lifecycle operations need a source event id")
        return self


class Scenario(FrozenModel):
    scenario_id: str
    audience: Literal["personal", "engineering"]
    capabilities: tuple[str, ...]
    steps: tuple[Step, ...]


class Fixture(FrozenModel):
    schema_id: Literal["atc.memory-acceptance.fixture.v1"] = Field(alias="schema")
    partition: Literal["development", "holdout"]
    synthetic: Literal[True]
    scenarios: tuple[Scenario, ...]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_fixture(partition: str, *, holdout_identity: str | None = None) -> tuple[Fixture, str]:
    if partition not in {"development", "holdout"}:
        raise ValueError("unknown fixture partition")
    if partition == "holdout" and not holdout_identity:
        raise ValueError("holdout requires a frozen candidate identity; never use it for tuning")
    path = ROOT / partition / "scenarios.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    actual = digest(raw)
    if manifest["partitions"][partition]["sha256"] != actual:
        raise ValueError("fixture changed after freeze; create a new version")
    fixture = Fixture.model_validate(raw)
    if fixture.partition != partition:
        raise ValueError("fixture partition mismatch")
    scenario_ids: set[str] = set()
    for scenario in fixture.scenarios:
        if scenario.scenario_id in scenario_ids:
            raise ValueError("duplicate scenario id")
        scenario_ids.add(scenario.scenario_id)
        events: set[str] = set()
        checkpoints: set[str] = set()
        for step in scenario.steps:
            if step.event is not None:
                if step.event.event_id in events:
                    raise ValueError("duplicate event id; use replay_event for retries")
                events.add(step.event.event_id)
            if step.event_id is not None and step.event_id not in events:
                raise ValueError("lifecycle operation refers to an unseen event")
            if step.request is not None:
                if step.request.checkpoint_id in checkpoints:
                    raise ValueError("duplicate checkpoint id")
                checkpoints.add(step.request.checkpoint_id)
                assert step.expect is not None
                for fact in step.expect.facts:
                    if not fact.sources or not set(fact.sources) <= events:
                        raise ValueError("oracle cites an unseen source")
                    re.compile(fact.pattern)
                for pattern in step.expect.forbidden_patterns:
                    re.compile(pattern)
    return fixture, actual


def grade(
    request: Request, oracle: Oracle, observation: Observation, events: dict[str, Event]
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []

    def fail(stage: str, code: str, detail: str) -> None:
        attribution = FailureAttribution(stage=stage, code=code)
        failures.append({"stage": attribution.stage, "code": attribution.code, "detail": detail})

    if not observation.supported:
        fail("compile", "UNSUPPORTED_SURFACE", observation.detail)
    for stage, chunks in (("retrieve", observation.retrieved), ("compile", observation.delivered)):
        for fact in oracle.facts:
            matching = [chunk for chunk in chunks if re.search(fact.pattern, chunk.text)]
            if not matching:
                fail(stage, "MISSING_FACT", fact.fact_id)
                continue
            supported = any(
                cite.source_event_id in fact.sources
                and cite.source_event_id in events
                and cite.quote
                and cite.quote in events[cite.source_event_id].text
                and re.search(fact.pattern, cite.quote)
                for chunk in matching
                for cite in chunk.citations
            )
            if not supported:
                fail(stage, "MISSING_CURRENT_PROVENANCE", fact.fact_id)
        for pattern in oracle.forbidden_patterns:
            if any(re.search(pattern, chunk.text, re.MULTILINE) for chunk in chunks):
                fail(stage, "FORBIDDEN_INFLUENCE", pattern)
        for chunk in chunks:
            for cite in chunk.citations:
                source = events.get(cite.source_event_id)
                if source is None or not cite.quote or cite.quote not in source.text:
                    fail(stage, "INVALID_CITATION", cite.source_event_id)
                elif source.project is not None and source.project != request.project:
                    fail("applicability", "CROSS_PROJECT_SOURCE", cite.source_event_id)
        if len(chunks) > 32 or sum(len(chunk.text) for chunk in chunks) > request.budget_chars:
            fail("budget", "CONTEXT_OVER_BUDGET", stage)
    for unknown in oracle.unknowns:
        if unknown not in observation.unknowns:
            fail("retrieve", "UNKNOWN_NOT_SIGNALLED", unknown)
    return failures


def run_fixture(
    factory: Callable[[], MemoryAdapter],
    root: Path,
    *,
    partition: str = "development",
    candidate_identity: str,
    holdout_identity: str | None = None,
) -> dict[str, Any]:
    fixture, fixture_sha = load_fixture(partition, holdout_identity=holdout_identity)
    cells: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    adapter_id = ""
    for scenario in fixture.scenarios:
        adapter = factory()
        adapter_id = adapter.adapter_id
        events: dict[str, Event] = {}
        adapter.reset(root / scenario.scenario_id, scenario.scenario_id)
        try:
            for step in scenario.steps:
                if step.event is not None:
                    events[step.event.event_id] = step.event
                    receipt = adapter.capture(step.event)
                    operations.append(
                        {
                            "scenario": scenario.scenario_id,
                            "operation": "capture",
                            "event_id": step.event.event_id,
                            **receipt.model_dump(),
                        }
                    )
                elif step.control is not None:
                    receipt = adapter.control(step.control, step.event_id)
                    operations.append(
                        {
                            "scenario": scenario.scenario_id,
                            "operation": step.control,
                            "event_id": step.event_id,
                            **receipt.model_dump(),
                        }
                    )
                else:
                    assert step.request is not None and step.expect is not None
                    observation = adapter.checkpoint(step.request)
                    failures = grade(step.request, step.expect, observation, events)
                    cells.append(
                        {
                            "scenario": scenario.scenario_id,
                            "checkpoint": step.request.checkpoint_id,
                            "surface": step.request.surface,
                            "passed": not failures,
                            "failures": failures,
                            "observation": observation.model_dump(mode="json"),
                        }
                    )
        finally:
            usage.append({"scenario": scenario.scenario_id, **adapter.usage().as_dict()})
            adapter.close()
    operation_failures = sum(item["status"] not in {"ok", "replayed"} for item in operations)
    passed = sum(cell["passed"] for cell in cells)
    return {
        "schema": "atc.memory-acceptance.report.v1",
        "adapter_abi": ABI,
        "adapter_id": adapter_id,
        "candidate_identity": candidate_identity,
        "fixture_sha256": fixture_sha,
        "partition": partition,
        "evidence": {
            "kind": "in_process_synthetic",
            "installed_clients": "UNRUN",
            "reader_model": "UNRUN",
            "competitor_engines": "NO_COMPARISON_CLAIM",
        },
        "usage_by_scenario": usage,
        "summary": {
            "checkpoints": len(cells),
            "passed": passed,
            "failed": len(cells) - passed,
            "operation_failures": operation_failures,
            "accepted": passed == len(cells) and operation_failures == 0,
        },
        "operations": operations,
        "checkpoints": cells,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default="bench.memory_acceptance_atc:AtcAdapter")
    parser.add_argument("--candidate-identity", required=True)
    parser.add_argument("--partition", choices=("development", "holdout"), default="development")
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scratch-root", type=Path, default=Path(".test-runs/memory-acceptance"))
    args = parser.parse_args(argv)
    if args.partition == "holdout" and not args.allow_holdout:
        parser.error("holdout requires --allow-holdout and a frozen candidate identity")
    module, separator, name = args.adapter.partition(":")
    if not separator:
        parser.error("adapter must be a trusted module:factory")
    factory = getattr(importlib.import_module(module), name)
    scratch_root = args.scratch_root.resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=scratch_root) as scratch:
        report = run_fixture(
            factory,
            Path(scratch),
            partition=args.partition,
            candidate_identity=args.candidate_identity,
            holdout_identity=(args.candidate_identity if args.allow_holdout else None),
        )
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0 if report["summary"]["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
