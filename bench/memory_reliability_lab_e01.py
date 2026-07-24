"""Run the bounded executable E01 longitudinal Memory Lab slice.

The governed condition is a new in-memory reference model.  It is not the
production Core and its results must not be described as current ATC results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from allthecontext.memory_reliability_lab import (
    LONGITUDINAL_FIXTURE_SCHEMA,
    REFERENCE_RULES,
    AppendLogSearchAdapter,
    AtcGovernedReferenceAdapter,
    CheckpointDescriptor,
    CheckpointOracle,
    EpisodeBudget,
    FailureAttribution,
    LogicalEvent,
    LongitudinalScenario,
    NoMemoryLongitudinalAdapter,
    run_e01_slice,
)

FIXTURES = Path(__file__).with_name("memory_reliability_lab_e01_fixtures.json")


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


def _event(value: Any) -> LogicalEvent:
    raw = _mapping(value, "event")
    return LogicalEvent(
        seq=int(raw["seq"]),
        at=str(raw["at"]),
        principal=str(raw["principal"]),
        source_class=str(raw["source_class"]),
        operation=str(raw["operation"]),
        object_id=str(raw["object_id"]),
        topic=_optional_string(raw.get("topic"), "topic"),
        role=_optional_string(raw.get("role"), "role"),
        value=_optional_string(raw.get("value"), "value"),
        project=_optional_string(raw.get("project"), "project"),
        domain=_optional_string(raw.get("domain"), "domain"),
        applies_to=_optional_string(raw.get("applies_to"), "applies_to"),
        supersedes=_optional_string(raw.get("supersedes"), "supersedes"),
        dependency=_optional_string(raw.get("dependency"), "dependency"),
    )


def _attribution(value: Any) -> FailureAttribution:
    raw = _mapping(value, "failure attribution")
    return FailureAttribution(stage=str(raw["stage"]), code=str(raw["code"]))


def _scenario(value: Any) -> LongitudinalScenario:
    raw = _mapping(value, "scenario")
    checkpoint = _mapping(raw["checkpoint"], "checkpoint")
    oracle = _mapping(raw["oracle"], "oracle")
    forbidden = _mapping(oracle.get("forbidden", {}), "forbidden")
    inventory_states = _mapping(oracle.get("inventory_states", {}), "inventory_states")
    inventory_absent = _mapping(oracle.get("inventory_absent", {}), "inventory_absent")
    events = raw.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    return LongitudinalScenario(
        scenario_id=str(raw["scenario_id"]),
        capabilities=_strings(raw["capabilities"], "capabilities"),
        events=tuple(_event(event) for event in events),
        checkpoint=CheckpointDescriptor(
            checkpoint_id=str(checkpoint["checkpoint_id"]),
            after_seq=int(checkpoint["after_seq"]),
            at=str(checkpoint["at"]),
            principal=str(checkpoint["principal"]),
            task_class=str(checkpoint["task_class"]),
            query_terms=_strings(checkpoint["query_terms"], "query_terms"),
            allowed_roles=_strings(checkpoint["allowed_roles"], "allowed_roles"),
            project=_optional_string(checkpoint.get("project"), "project"),
            domain=_optional_string(checkpoint.get("domain"), "domain"),
        ),
        oracle=CheckpointOracle(
            required_values=frozenset(
                _strings(oracle.get("required_values", []), "required_values")
            ),
            forbidden={
                str(object_id): _attribution(attribution)
                for object_id, attribution in forbidden.items()
            },
            expects_abstention=bool(oracle.get("expects_abstention", False)),
            inventory_states={
                str(object_id): str(state) for object_id, state in inventory_states.items()
            },
            inventory_absent={
                str(object_id): _attribution(attribution)
                for object_id, attribution in inventory_absent.items()
            },
        ),
    )


def load_fixture(
    path: Path = FIXTURES,
) -> tuple[tuple[LongitudinalScenario, ...], EpisodeBudget]:
    """Load the frozen symbolic partial-E01 fixture."""

    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    root = _mapping(loaded, "fixture")
    if root.get("schema") != LONGITUDINAL_FIXTURE_SCHEMA:
        raise ValueError("unsupported E01 longitudinal fixture schema")
    scenarios = root.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("scenarios must be a list")
    budget = _mapping(root.get("budget"), "budget")
    return (
        tuple(_scenario(scenario) for scenario in scenarios),
        EpisodeBudget(
            max_items=int(budget["max_items"]),
            max_token_units=int(budget["max_token_units"]),
        ),
    )


def _ablation(rule: str) -> AtcGovernedReferenceAdapter:
    return AtcGovernedReferenceAdapter(
        condition_id=f"ablation_without_{rule}",
        enabled_rules=REFERENCE_RULES - {rule},
    )


def run_fixture(*, repeats: int = 2) -> dict[str, Any]:
    """Compare controls, the reference model, and one-rule-removal ablations."""

    scenarios, budget = load_fixture()
    return run_e01_slice(
        scenarios,
        (
            NoMemoryLongitudinalAdapter(),
            AppendLogSearchAdapter(),
            AtcGovernedReferenceAdapter(),
            _ablation("authority"),
            _ablation("currentness_invalidation"),
            _ablation("applicability"),
            _ablation("purge_closure"),
        ),
        fixture_sha256=hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        budget=budget,
        repeats=repeats,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_fixture(repeats=args.repeats)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
