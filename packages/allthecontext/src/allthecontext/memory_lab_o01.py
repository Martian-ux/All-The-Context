"""Deterministic synthetic O01 online/off-policy/distribution-shift research.

This module is an isolated, in-memory experiment. It does not exercise the
operator Core, create canonical records, define a production schema, or model
personal context. Pre-action inputs omit oracle actions; after an action, the
synthetic environment can provide explicit supervised corrective-oracle feedback.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

O01_FIXTURE_SCHEMA = "atc.memory-lab.o01-fixture.v1"
O01_REPORT_SCHEMA = "atc.memory-lab.o01-report.v1"
ConditionKind = Literal["no_memory", "append_log", "stable_current_state", "governed"]


@dataclass(frozen=True, slots=True)
class Budget:
    """Equal logical budget applied to every condition and regime."""

    max_writes_per_step: int
    max_reads_per_step: int
    max_selected_items: int
    max_memory_items: int

    def __post_init__(self) -> None:
        if min(asdict(self).values()) < 1:
            raise ValueError("all O01 budgets must be positive")


@dataclass(frozen=True, slots=True)
class VisibleWrite:
    """A condition-local write candidate with no oracle action field."""

    state: str
    action: str
    epoch: str
    trusted: bool
    applies: bool
    source: str


@dataclass(frozen=True, slots=True)
class VisibleStep:
    """The only pre-action data supplied to a condition."""

    clock: int
    state: str
    epoch: str
    write_candidates: tuple[VisibleWrite, ...]


@dataclass(frozen=True, slots=True)
class VisibleOutcome:
    """Post-action feedback that can expose the corrective oracle action."""

    clock: int
    state: str
    epoch: str
    chosen_action: str
    accepted: bool
    corrective_action: str | None


@dataclass(frozen=True, slots=True)
class HiddenStep:
    """Harness-only action oracle, never passed to a condition."""

    visible: VisibleStep
    expected_action: str


@dataclass(frozen=True, slots=True)
class Regime:
    name: str
    preloaded: tuple[VisibleWrite, ...]
    steps: tuple[HiddenStep, ...]
    shift_clock: int | None


@dataclass(frozen=True, slots=True)
class FrozenProtocol:
    repeats: int
    budget: Budget
    recovery_consecutive: int
    rank_move_hold_threshold: int
    spearman_hold_threshold: float
    shifted_caos_gap_hold_threshold: float
    regimes: tuple[Regime, ...]


@dataclass(frozen=True, slots=True)
class StoredItem:
    state: str
    action: str
    epoch: str
    trusted: bool
    applies: bool
    sequence: int


@dataclass(frozen=True, slots=True)
class ReadResult:
    action: str | None
    stale: bool


@dataclass(frozen=True, slots=True)
class ConditionSpec:
    condition_id: str
    kind: ConditionKind
    rules: frozenset[str]
    non_production_reference: bool = False
    feedback_enabled: bool = True


FULL_REFERENCE_RULES = frozenset({"admission", "currentness", "applicability", "utilization"})
PRIMARY_CONDITIONS = (
    ConditionSpec("no_memory", "no_memory", frozenset()),
    ConditionSpec("append_log", "append_log", frozenset()),
    ConditionSpec("stable_current_state", "stable_current_state", frozenset()),
    ConditionSpec(
        "atc_governed_in_memory_reference",
        "governed",
        FULL_REFERENCE_RULES,
        non_production_reference=True,
    ),
)
ABLATION_CONDITIONS = tuple(
    ConditionSpec(
        f"reference_without_{rule}",
        "governed",
        FULL_REFERENCE_RULES - {rule},
        non_production_reference=True,
    )
    for rule in sorted(FULL_REFERENCE_RULES)
)
NO_FEEDBACK_CONDITION = ConditionSpec(
    "reference_without_post_action_feedback",
    "governed",
    FULL_REFERENCE_RULES,
    non_production_reference=True,
    feedback_enabled=False,
)


class SyntheticMemoryCondition:
    """Small condition implementation whose state is isolated per run."""

    def __init__(self, spec: ConditionSpec, budget: Budget) -> None:
        self.spec = spec
        self._budget = budget
        self._items: list[StoredItem] = []
        self._sequence = 0

    def write(self, candidate: VisibleWrite) -> bool:
        if self.spec.kind == "no_memory":
            return False
        if self.spec.kind == "governed":
            if "admission" in self.spec.rules and not candidate.trusted:
                return False
            if "applicability" in self.spec.rules and not candidate.applies:
                return False
        self._sequence += 1
        item = StoredItem(
            state=candidate.state,
            action=candidate.action,
            epoch=candidate.epoch,
            trusted=candidate.trusted,
            applies=candidate.applies,
            sequence=self._sequence,
        )
        if self.spec.kind in {"stable_current_state", "governed"}:
            self._items = [existing for existing in self._items if existing.state != item.state]
        self._items.append(item)
        self._items = self._items[-self._budget.max_memory_items :]
        return True

    def read(self, step: VisibleStep) -> ReadResult:
        if self.spec.kind == "no_memory":
            return ReadResult(None, False)
        candidates = [item for item in self._items if item.state == step.state]
        if self.spec.kind == "stable_current_state":
            candidates = [item for item in candidates if item.epoch == step.epoch]
        if self.spec.kind == "governed":
            if "currentness" in self.spec.rules:
                candidates = [item for item in candidates if item.epoch == step.epoch]
            if "applicability" in self.spec.rules:
                candidates = [item for item in candidates if item.applies]
        if not candidates:
            return ReadResult(None, False)
        selected_items = sorted(
            candidates,
            key=lambda item: item.sequence,
            reverse=True,
        )[: self._budget.max_selected_items]
        selected = selected_items[0]
        action: str | None = selected.action
        if self.spec.kind == "governed" and "utilization" not in self.spec.rules:
            action = None
        return ReadResult(action, selected.epoch != step.epoch)


def _opaque(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError(f"{name} must be a non-empty ASCII opaque symbol")
    if any(character.isspace() for character in value):
        raise ValueError(f"{name} must not contain whitespace")
    return value


def _load_write(value: Any) -> VisibleWrite:
    if not isinstance(value, dict):
        raise ValueError("write candidates must be objects")
    return VisibleWrite(
        state=_opaque(value["state"], "state"),
        action=_opaque(value["action"], "action"),
        epoch=_opaque(value["epoch"], "epoch"),
        trusted=bool(value["trusted"]),
        applies=bool(value["applies"]),
        source=_opaque(value["source"], "source"),
    )


def load_protocol(path: Path) -> FrozenProtocol:
    """Load and validate the frozen synthetic protocol."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != O01_FIXTURE_SCHEMA:
        raise ValueError("unsupported O01 fixture schema")
    config = raw["frozen_protocol"]
    budget = Budget(**config["equal_budget"])
    regimes: list[Regime] = []
    for raw_regime in raw["regimes"]:
        steps: list[HiddenStep] = []
        for raw_step in raw_regime["steps"]:
            visible = VisibleStep(
                clock=int(raw_step["clock"]),
                state=_opaque(raw_step["state"], "state"),
                epoch=_opaque(raw_step["epoch"], "epoch"),
                write_candidates=tuple(
                    _load_write(candidate) for candidate in raw_step.get("write_candidates", [])
                ),
            )
            steps.append(
                HiddenStep(
                    visible=visible,
                    expected_action=_opaque(raw_step["oracle"]["expected_action"], "action"),
                )
            )
        regimes.append(
            Regime(
                name=_opaque(raw_regime["name"], "regime"),
                preloaded=tuple(_load_write(item) for item in raw_regime.get("preloaded", [])),
                steps=tuple(steps),
                shift_clock=raw_regime.get("shift_clock"),
            )
        )
    protocol = FrozenProtocol(
        repeats=int(config["repeats"]),
        budget=budget,
        recovery_consecutive=int(config["recovery_consecutive"]),
        rank_move_hold_threshold=int(config["rank_move_hold_threshold"]),
        spearman_hold_threshold=float(config["spearman_hold_threshold"]),
        shifted_caos_gap_hold_threshold=float(config["shifted_caos_gap_hold_threshold"]),
        regimes=tuple(regimes),
    )
    if protocol.repeats < 2 or protocol.recovery_consecutive < 1:
        raise ValueError("O01 requires deterministic repeats and a positive recovery window")
    if {regime.name for regime in protocol.regimes} != {"off_policy", "online", "shifted"}:
        raise ValueError("O01 requires off_policy, online, and shifted regimes")
    return protocol


def _environment_outcome(step: HiddenStep, chosen: str) -> VisibleOutcome:
    """Reveal generic condition-local feedback only after action evaluation."""

    successful = chosen == step.expected_action
    return VisibleOutcome(
        clock=step.visible.clock,
        state=step.visible.state,
        epoch=step.visible.epoch,
        chosen_action=chosen,
        accepted=successful,
        corrective_action=None if successful else step.expected_action,
    )


def _outcome_write(outcome: VisibleOutcome) -> VisibleWrite:
    """Convert explicit environment feedback into an upstream write signal."""

    action = outcome.chosen_action if outcome.accepted else outcome.corrective_action
    if action is None:
        raise ValueError("rejected outcomes require an explicit corrective action")
    return VisibleWrite(
        state=outcome.state,
        action=action,
        epoch=outcome.epoch,
        trusted=True,
        applies=True,
        source="supervised_post_action_corrective_oracle_feedback",
    )


def _run_once(
    spec: ConditionSpec,
    regime: Regime,
    budget: Budget,
    recovery_consecutive: int,
) -> dict[str, Any]:
    condition = SyntheticMemoryCondition(spec, budget)
    writes_offered = 0
    correct_admission_decisions = 0
    admitted = 0
    reads = 0
    correct_reads = 0
    utilized_reads = 0
    correct_actions = 0
    stale_activations = 0
    post_shift_successes: list[bool] = []
    max_write_attempts_observed = 0
    max_read_attempts_observed = 0
    max_selected_items_observed = 0

    for candidate in regime.preloaded:
        writes_offered += 1
        did_admit = condition.write(candidate)
        admitted += int(did_admit)
        should_admit = candidate.trusted and candidate.applies
        correct_admission_decisions += int(did_admit == should_admit)

    for hidden in regime.steps:
        visible = hidden.visible
        step_write_attempts = 0
        for candidate in visible.write_candidates:
            if step_write_attempts >= budget.max_writes_per_step:
                break
            writes_offered += 1
            step_write_attempts += 1
            did_admit = condition.write(candidate)
            admitted += int(did_admit)
            should_admit = candidate.trusted and candidate.applies
            correct_admission_decisions += int(did_admit == should_admit)

        read = condition.read(visible)
        max_read_attempts_observed = max(max_read_attempts_observed, 1)
        reads += int(read.action is not None)
        max_selected_items_observed = max(max_selected_items_observed, int(read.action is not None))
        correct_reads += int(read.action == hidden.expected_action)
        stale_activations += int(read.stale and read.action is not None)
        fallback = "A0"
        chosen = read.action or fallback
        utilized_reads += int(read.action is not None and chosen == read.action)
        success = chosen == hidden.expected_action
        correct_actions += int(success)
        if regime.shift_clock is not None and visible.clock >= regime.shift_clock:
            post_shift_successes.append(success)

        outcome = _environment_outcome(hidden, chosen)
        if spec.feedback_enabled and step_write_attempts < budget.max_writes_per_step:
            candidate = _outcome_write(outcome)
            writes_offered += 1
            step_write_attempts += 1
            did_admit = condition.write(candidate)
            admitted += int(did_admit)
            correct_admission_decisions += int(did_admit)
        max_write_attempts_observed = max(max_write_attempts_observed, step_write_attempts)

    recovery_at: int | None = None
    window = 0
    for offset, success in enumerate(post_shift_successes, 1):
        window = window + 1 if success else 0
        if window >= recovery_consecutive:
            recovery_at = offset
            break
    step_count = len(regime.steps)
    return {
        "write_opportunities": writes_offered,
        "write_admitted_count": admitted,
        "write_admission_accuracy": round(
            correct_admission_decisions / writes_offered if writes_offered else 1.0, 6
        ),
        "later_read_count": reads,
        "correct_later_read_coverage": round(correct_reads / step_count, 6),
        "later_read_precision": round(correct_reads / reads if reads else 0.0, 6),
        "read_utilization_rate": round(utilized_reads / reads if reads else 0.0, 6),
        "correct_next_action_count": correct_actions,
        "caos": round(correct_actions / step_count, 6),
        "post_shift_stale_activation_count": stale_activations,
        "recovery_at_t": recovery_at,
        "budget_observed": {
            "max_write_attempts_per_step": max_write_attempts_observed,
            "max_read_attempts_per_step": max_read_attempts_observed,
            "max_selected_items": max_selected_items_observed,
        },
    }


def _rank_conditions(results: Mapping[str, Mapping[str, Any]], regime: str) -> list[str]:
    """Return a deterministic display order; this order is not used for decisions."""

    complexity = {
        "no_memory": 0,
        "append_log": 1,
        "stable_current_state": 2,
        "atc_governed_in_memory_reference": 3,
    }
    return sorted(
        results,
        key=lambda condition_id: (
            -float(results[condition_id][regime]["caos"]),
            complexity[condition_id],
        ),
    )


def _average_ranks(
    results: Mapping[str, Mapping[str, Any]],
    regime: str,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Assign average ranks to exact CAOS ties."""

    grouped: dict[float, list[str]] = {}
    for condition_id, condition_result in results.items():
        score = float(condition_result[regime]["caos"])
        grouped.setdefault(score, []).append(condition_id)
    ranks: dict[str, float] = {}
    tie_groups: list[dict[str, Any]] = []
    next_position = 1
    for score in sorted(grouped, reverse=True):
        conditions = sorted(grouped[score])
        final_position = next_position + len(conditions) - 1
        average_rank = (next_position + final_position) / 2.0
        for condition_id in conditions:
            ranks[condition_id] = average_rank
        tie_groups.append(
            {
                "caos": score,
                "average_rank": average_rank,
                "conditions": conditions,
            }
        )
        next_position = final_position + 1
    return ranks, tie_groups


def _pearson_rank_correlation(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    """Compute tie-aware Spearman as Pearson correlation of average ranks."""

    if set(left) != set(right) or len(left) < 2:
        raise ValueError("rank correlations must cover the same conditions")
    conditions = sorted(left)
    left_mean = sum(left.values()) / len(left)
    right_mean = sum(right.values()) / len(right)
    left_delta = [left[condition] - left_mean for condition in conditions]
    right_delta = [right[condition] - right_mean for condition in conditions]
    numerator = sum(a * b for a, b in zip(left_delta, right_delta, strict=True))
    left_variance = sum(value**2 for value in left_delta)
    right_variance = sum(value**2 for value in right_delta)
    denominator = math.sqrt(left_variance * right_variance)
    if denominator == 0.0:
        raise ValueError("rank correlation is undefined when every condition is tied")
    return round(numerator / denominator, 6)


def _condition_report(
    spec: ConditionSpec, protocol: FrozenProtocol
) -> tuple[dict[str, Any], list[str]]:
    first: dict[str, Any] | None = None
    fingerprints: list[str] = []
    for _ in range(protocol.repeats):
        result = {
            regime.name: _run_once(
                spec,
                regime,
                protocol.budget,
                protocol.recovery_consecutive,
            )
            for regime in protocol.regimes
        }
        rendered = json.dumps(result, sort_keys=True, separators=(",", ":"))
        fingerprints.append(hashlib.sha256(rendered.encode("utf-8")).hexdigest())
        if first is None:
            first = result
    assert first is not None
    return first, fingerprints


def run_o01(protocol: FrozenProtocol, fixture_sha256: str) -> dict[str, Any]:
    """Execute O01 and return an identifier-safe aggregate report."""

    primary: dict[str, Any] = {}
    deterministic: dict[str, bool] = {}
    for spec in PRIMARY_CONDITIONS:
        primary[spec.condition_id], fingerprints = _condition_report(spec, protocol)
        deterministic[spec.condition_id] = len(set(fingerprints)) == 1

    presentation_order = {
        regime.name: _rank_conditions(primary, regime.name) for regime in protocol.regimes
    }
    decision_ranks: dict[str, dict[str, float]] = {}
    tie_groups: dict[str, list[dict[str, Any]]] = {}
    for regime in protocol.regimes:
        decision_ranks[regime.name], tie_groups[regime.name] = _average_ranks(
            primary,
            regime.name,
        )
    correlations = {
        "off_policy_to_online": _pearson_rank_correlation(
            decision_ranks["off_policy"], decision_ranks["online"]
        ),
        "off_policy_to_shifted": _pearson_rank_correlation(
            decision_ranks["off_policy"], decision_ranks["shifted"]
        ),
        "online_to_shifted": _pearson_rank_correlation(
            decision_ranks["online"], decision_ranks["shifted"]
        ),
    }
    rank_movements = {
        condition: max(values) - min(values)
        for condition in primary
        for values in (
            [decision_ranks[regime][condition] for regime in ("off_policy", "online", "shifted")],
        )
    }
    max_rank_move = round(max(rank_movements.values()), 6)
    strongest_simple_shifted = max(
        primary[condition]["shifted"]["caos"]
        for condition in ("no_memory", "append_log", "stable_current_state")
    )
    reference_shifted = primary["atc_governed_in_memory_reference"]["shifted"]["caos"]
    shifted_gap = round(strongest_simple_shifted - reference_shifted, 6)
    unstable_recovery = any(
        primary[condition]["shifted"]["recovery_at_t"] is None
        for condition in primary
        if condition != "no_memory"
    )
    hold_reasons: list[str] = []
    if max_rank_move > protocol.rank_move_hold_threshold:
        hold_reasons.append("rank_move_exceeds_frozen_threshold")
    if min(correlations.values()) < protocol.spearman_hold_threshold:
        hold_reasons.append("spearman_below_frozen_threshold")
    if unstable_recovery:
        hold_reasons.append("recovery_unstable")
    if shifted_gap > protocol.shifted_caos_gap_hold_threshold:
        hold_reasons.append("reference_shifted_caos_gap_exceeds_frozen_threshold")

    ablations: dict[str, Any] = {}
    reference = primary["atc_governed_in_memory_reference"]
    for spec in ABLATION_CONDITIONS:
        result, fingerprints = _condition_report(spec, protocol)
        removed_rule = next(iter(FULL_REFERENCE_RULES - spec.rules))
        ablations[spec.condition_id] = {
            "removed_rule": removed_rule,
            "repeat_deterministic": len(set(fingerprints)) == 1,
            "regime_caos_delta_from_full_reference": {
                regime.name: round(reference[regime.name]["caos"] - result[regime.name]["caos"], 6)
                for regime in protocol.regimes
            },
            "write_admission_accuracy_delta_from_full_reference": round(
                reference["online"]["write_admission_accuracy"]
                - result["online"]["write_admission_accuracy"],
                6,
            ),
            "shifted_stale_activation_delta_from_full_reference": (
                result["shifted"]["post_shift_stale_activation_count"]
                - reference["shifted"]["post_shift_stale_activation_count"]
            ),
        }
    no_feedback, feedback_fingerprints = _condition_report(NO_FEEDBACK_CONDITION, protocol)
    condition_ablations = {
        NO_FEEDBACK_CONDITION.condition_id: {
            "removed_condition": "supervised_post_action_corrective_oracle_feedback",
            "repeat_deterministic": len(set(feedback_fingerprints)) == 1,
            "regime_caos_delta_from_full_reference": {
                regime.name: round(
                    reference[regime.name]["caos"] - no_feedback[regime.name]["caos"],
                    6,
                )
                for regime in protocol.regimes
            },
        }
    }

    return {
        "schema": O01_REPORT_SCHEMA,
        "experiment": "O01",
        "fixture_sha256": fixture_sha256,
        "evidence_level": "L1_isolated_synthetic_worker_result",
        "execution_scope": {
            "synthetic_opaque_data_only": True,
            "production_core_touched": False,
            "production_core_semantics_claimed": False,
            "external_code_models_or_network": False,
            "condition_state_isolated": True,
            "pre_action_oracle_exposed": False,
            "post_action_corrective_oracle_exposed": True,
            "wall_clock_access": False,
        },
        "equal_budget": asdict(protocol.budget),
        "frozen_clock": "integer_fixture_clock",
        "frozen_shift_protocol": {
            "shift_regime_count": sum(
                regime.shift_clock is not None for regime in protocol.regimes
            ),
            "shift_clock": next(
                regime.shift_clock for regime in protocol.regimes if regime.shift_clock is not None
            ),
            "recovery_consecutive": protocol.recovery_consecutive,
        },
        "repeats": protocol.repeats,
        "conditions": {
            spec.condition_id: {
                "label": (
                    "non-production governed in-memory reference"
                    if spec.non_production_reference
                    else spec.kind
                ),
                "rules": sorted(spec.rules),
                "repeat_deterministic": deterministic[spec.condition_id],
                "regimes": primary[spec.condition_id],
            }
            for spec in PRIMARY_CONDITIONS
        },
        "presentation_order": presentation_order,
        "decision_average_ranks": decision_ranks,
        "decision_tie_groups": tie_groups,
        "tie_aware_spearman": correlations,
        "max_rank_move": max_rank_move,
        "condition_rule_ablations": ablations,
        "condition_ablations": condition_ablations,
        "decision": {
            "state": "HOLD" if hold_reasons else "GO_FOR_FURTHER_EVIDENCE",
            "hold_reasons": hold_reasons,
            "frozen_criteria": {
                "rank_move_greater_than": protocol.rank_move_hold_threshold,
                "spearman_less_than": protocol.spearman_hold_threshold,
                "recovery_must_be_stable": True,
                "shifted_caos_gap_greater_than": protocol.shifted_caos_gap_hold_threshold,
            },
        },
        "privacy": {
            "raw_fixture_values_in_report": False,
            "step_or_state_identifiers_in_report": False,
            "condition_identifiers_are_public_experiment_labels": True,
        },
        "validity_limitations": [
            "small_hand_authored_binary_action_environment",
            "fixture_and_reference_rules_codesigned",
            "trusted_and_applies_are_fixture_supplied_upstream_governance_signals",
            "upstream_signal_availability_and_accuracy_are_not_established",
            "admission_is_rule_based_not_learned_or_generic",
            "post_action_feedback_is_supervised_corrective_oracle_feedback",
            "binary_fixture_feedback_exposes_the_expected_action_after_failure",
            "corrective_feedback_does_not_establish_generic_answer_recovery",
            "deterministic_symbolic_policy_not_model_backed",
            "off_policy_replay_is_neutral_synthetic_trace_not_logged_product_behavior",
            "no_current_core_behavior_was_exercised",
            "presentation_ties_use_frozen_simplicity_order_but_decisions_use_average_ranks",
        ],
    }


def run_o01_file(path: Path) -> dict[str, Any]:
    """Load, hash, and execute a frozen O01 fixture."""

    fixture_bytes = path.read_bytes()
    return run_o01(load_protocol(path), hashlib.sha256(fixture_bytes).hexdigest())
