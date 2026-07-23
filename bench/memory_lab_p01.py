"""Run the offline deterministic P01 admission-to-action poisoning slice."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from allthecontext.memory_lab_p01 import (
    P01_FIXTURE_SCHEMA,
    P01_REFERENCE_RULES,
    ConservativeGovernedP01ReferenceAdapter,
    EagerAppendOnlyP01Adapter,
    NoMemoryP01Adapter,
    P01Budget,
    P01Event,
    P01Oracle,
    P01Scenario,
    P01Task,
    run_p01_slice,
)

FIXTURES = Path(__file__).with_name("memory_lab_p01_fixtures.json")


@dataclass(frozen=True, slots=True)
class P01FixtureConfig:
    """Frozen execution parameters loaded separately from adapter-visible data."""

    repeats: int
    poisoned_write_hold_threshold: float


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a string list")
    return tuple(value)


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value


def _event(value: Any) -> P01Event:
    raw = _mapping(value, "event")
    return P01Event(
        seq=int(raw["seq"]),
        operation=str(raw["operation"]),
        event_id=str(raw["event_id"]),
        principal=str(raw["principal"]),
        channel=_optional_string(raw.get("channel"), "channel"),
        source_class=_optional_string(raw.get("source_class"), "source_class"),
        witness=_optional_string(raw.get("witness"), "witness"),
        record_id=_optional_string(raw.get("record_id"), "record_id"),
        role=_optional_string(raw.get("role"), "role"),
        requested_force=_optional_string(
            raw.get("requested_force"),
            "requested_force",
        ),
        topic=_optional_string(raw.get("topic"), "topic"),
        payload=_optional_string(raw.get("payload"), "payload"),
        action_class=_optional_string(raw.get("action_class"), "action_class"),
        project=_optional_string(raw.get("project"), "project"),
        applies_to=_optional_string(raw.get("applies_to"), "applies_to"),
        target_record_id=_optional_string(
            raw.get("target_record_id"),
            "target_record_id",
        ),
    )


def _scenario(value: Any) -> P01Scenario:
    raw = _mapping(value, "scenario")
    task = _mapping(raw["task"], "task")
    oracle = _mapping(raw["oracle"], "oracle")
    events = raw.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    return P01Scenario(
        scenario_id=str(raw["scenario_id"]),
        channel=str(raw["channel"]),
        capabilities=_strings(raw["capabilities"], "capabilities"),
        events=tuple(_event(event) for event in events),
        task=P01Task(
            checkpoint_id=str(task["checkpoint_id"]),
            after_seq=int(task["after_seq"]),
            principal=str(task["principal"]),
            query_topic=str(task["query_topic"]),
            project=str(task["project"]),
            task_class=str(task["task_class"]),
        ),
        oracle=P01Oracle(
            case_kind=str(oracle["case_kind"]),
            target_record_id=str(oracle["target_record_id"]),
            target_action=str(oracle["target_action"]),
        ),
    )


def load_fixture(
    path: Path = FIXTURES,
) -> tuple[tuple[P01Scenario, ...], P01Budget, P01FixtureConfig]:
    """Load the frozen symbolic fixture and harness-only preregistration."""

    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    root = _mapping(loaded, "fixture")
    if root.get("schema") != P01_FIXTURE_SCHEMA:
        raise ValueError("unsupported P01 fixture schema")
    scenarios = root.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("scenarios must be a list")
    budget = _mapping(root.get("budget"), "budget")
    preregistration = _mapping(root.get("preregistration"), "preregistration")
    return (
        tuple(_scenario(scenario) for scenario in scenarios),
        P01Budget(max_items=int(budget["max_items"])),
        P01FixtureConfig(
            repeats=int(preregistration["repeats"]),
            poisoned_write_hold_threshold=float(
                preregistration[
                    "poisoned_durable_write_one_sided_95_upper_hold_threshold"
                ]
            ),
        ),
    )


def _ablation(rule: str) -> ConservativeGovernedP01ReferenceAdapter:
    return ConservativeGovernedP01ReferenceAdapter(
        condition_id=f"ablation_without_{rule}",
        enabled_rules=P01_REFERENCE_RULES - {rule},
    )


def run_fixture(*, repeats: int | None = None) -> dict[str, Any]:
    """Run controls, the non-production reference, and one-rule ablations."""

    scenarios, budget, config = load_fixture()
    return run_p01_slice(
        scenarios,
        (
            NoMemoryP01Adapter(),
            EagerAppendOnlyP01Adapter(),
            ConservativeGovernedP01ReferenceAdapter(),
            _ablation("channel_role_admission"),
            _ablation("currentness_revocation"),
            _ablation("task_applicability"),
            _ablation("protected_action_confirmation"),
        ),
        fixture_sha256=hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        budget=budget,
        repeats=config.repeats if repeats is None else repeats,
        poisoned_write_hold_threshold=config.poisoned_write_hold_threshold,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_fixture(repeats=args.repeats)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
