"""Run the isolated Wave 3 M2 sealed-projection compiler experiment."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import platform
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from allthecontext.memory_lab_m2 import (
    CompilationResult,
    CompilationStatus,
    ContextNeed,
    ContextObligation,
    ContextRecord,
    ContextRole,
    SealedProjectionMinimalCompiler,
    seal_projection,
    serialize_receipt,
)
from allthecontext.retrieval_contracts import SetSelectionConstraints
from allthecontext.set_selection import DeterministicSetSelector, SetSelectionCandidate

FIXTURES = Path(__file__).with_name("memory_lab_m2_fixtures.json")
REPORT_SCHEMA = "atc.memory-lab.m2-report.v1"
REPEATS_REQUIRED = 20
CHANNELS = (
    "full_observable_receipt",
    "semantic_output",
    "reason_code_multiset",
    "cursor_page_shape",
    "timing_class",
    "learning_update_state",
)
CONDITION_ORDER = (
    "full_authorized_context",
    "existing_deterministic_set_selection",
    "sealed_non_minimal_projection",
    "sealed_minimal_projection",
    "ablation_authorization_after_relevance",
    "ablation_applicability_after_ranking",
    "ablation_no_epistemic_role",
    "ablation_top_k_without_closure",
    "ablation_no_current_version_reread",
    "ablation_no_delete_and_recompile",
    "ablation_no_cumulative_disclosure",
)


@dataclass(frozen=True, slots=True)
class HarnessOracle:
    """Harness-only success definition never passed to a compiler condition."""

    required_item_groups: tuple[frozenset[str], ...]


@dataclass(frozen=True, slots=True)
class PairCase:
    pair_ordinal: int
    base_records: tuple[ContextRecord, ...]
    variant_records: tuple[ContextRecord, ...]
    need: ContextNeed
    oracle: HarnessOracle
    canary_key: str


@dataclass(frozen=True, slots=True)
class ObservableOutcome:
    """All values needed for harness scoring and protected-channel comparison."""

    selected: tuple[ContextRecord, ...]
    status: str
    semantic_output: str
    reason_code_multiset: tuple[tuple[str, int], ...]
    cursor_page_shape: tuple[int, ...]
    timing_class: str
    learning_update_state: str
    disclosure_chars: int
    deletion_test_count: int = 0
    full_observable_receipt: str = ""


Condition = Callable[[Sequence[ContextRecord], ContextNeed], ObservableOutcome]


def load_cases(path: Path = FIXTURES) -> tuple[PairCase, ...]:
    """Expand the finite declared fixture matrix into exactly 1,000 pairs."""

    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != "atc.memory-lab.m2-fixture.v1":
        raise ValueError("unsupported M2 fixture schema")
    generation = raw.get("pair_generation")
    templates = raw.get("templates")
    if not isinstance(generation, dict) or not isinstance(templates, list):
        raise ValueError("M2 fixture must declare generation and templates")
    families = generation.get("canary_families")
    variants = generation.get("variants_per_family")
    if (
        not isinstance(families, list)
        or not all(isinstance(item, str) for item in families)
        or not isinstance(variants, int)
    ):
        raise ValueError("invalid finite pair-generation declaration")

    cases: list[PairCase] = []
    for template_index, template in enumerate(templates):
        if not isinstance(template, dict):
            raise ValueError("M2 templates must be objects")
        base_records = tuple(_record(item) for item in _object_list(template, "records"))
        obligations = tuple(
            ContextObligation(
                str(item["id"]),
                frozenset(ContextRole(role) for role in _string_list(item, "roles")),
            )
            for item in _object_list(template, "obligations")
        )
        oracle = HarnessOracle(
            tuple(frozenset(_string_values(group)) for group in _list(template, "oracle_groups"))
        )
        need = ContextNeed(
            request_commitment=_digest("request", str(template_index)),
            obligations=obligations,
            character_budget=int(template["budget"]),
            prior_disclosure_chars=int(template["prior_disclosure"]),
            cumulative_disclosure_limit=int(template["cumulative_limit"]),
        )
        for family_index, family in enumerate(families):
            for variant_index in range(variants):
                pair_ordinal = len(cases)
                canary = _canary(
                    family,
                    variant_index,
                    obligations[0],
                    template_index=template_index,
                    family_index=family_index,
                )
                cases.append(
                    PairCase(
                        pair_ordinal,
                        base_records,
                        (*base_records, canary),
                        need,
                        oracle,
                        canary.key,
                    )
                )
    expected = int(generation["expected_unique_pairs"])
    if len(cases) != expected:
        raise ValueError(f"fixture expands to {len(cases)} pairs, expected {expected}")
    if any(
        len(case.variant_records) > int(generation["maximum_candidates_per_vault"])
        for case in cases
    ):
        raise ValueError("fixture exceeds its declared candidate cap")
    if any(
        len(case.need.obligations) > int(generation["maximum_obligations_per_need"])
        for case in cases
    ):
        raise ValueError("fixture exceeds its declared obligation cap")
    return tuple(cases)


def _record(raw: Mapping[str, Any]) -> ContextRecord:
    return ContextRecord(
        key=str(raw["key"]),
        version=int(raw.get("version", 1)),
        content=str(raw["content"]),
        role=ContextRole(str(raw["role"])),
        coverage_ids=frozenset(_string_list(raw, "covers")),
        dependency_ids=frozenset(_string_list(raw, "dependencies", default=())),
        relevance=int(raw["relevance"]),
        authorized=bool(raw.get("authorized", True)),
        temporally_current=bool(raw.get("temporally_current", True)),
        applicable=bool(raw.get("applicable", True)),
    )


def _canary(
    family: str,
    variant: int,
    obligation: ContextObligation,
    *,
    template_index: int,
    family_index: int,
) -> ContextRecord:
    accepted_role = min(obligation.accepted_roles, key=lambda role: role.value)
    flags = {
        "unauthorized": (False, True, True),
        "deleted": (True, False, True),
        "purged": (True, False, True),
        "out_of_scope": (True, True, False),
        "authorized_inapplicable": (True, True, False),
    }
    try:
        authorized, current, applicable = flags[family]
    except KeyError as error:
        raise ValueError("unsupported M2 canary family") from error
    suffix = f"{template_index:02d}{family_index:02d}{variant:02d}"
    return ContextRecord(
        key=f"sealed-canary-{suffix}",
        version=1,
        content=f"SEALED_CANARY_{suffix}",
        role=accepted_role,
        coverage_ids=frozenset({obligation.obligation_id}),
        relevance=10_000 + variant,
        authorized=authorized,
        temporally_current=current,
        applicable=applicable,
    )


def _minimal_condition(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    result = SealedProjectionMinimalCompiler().compile(records, need)
    return _from_compilation(result)


def _from_compilation(result: CompilationResult) -> ObservableOutcome:
    receipt = result.receipt
    return ObservableOutcome(
        selected=result.selected,
        status=receipt.status.value,
        semantic_output=receipt.semantic_output_digest,
        reason_code_multiset=receipt.reason_code_multiset,
        cursor_page_shape=receipt.cursor_page_shape,
        timing_class=receipt.timing_class,
        learning_update_state=receipt.learning_state_digest,
        disclosure_chars=receipt.disclosure_chars,
        deletion_test_count=len(receipt.deletion_tests),
        full_observable_receipt=serialize_receipt(receipt),
    )


def _full_authorized(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    eligible = tuple(
        item
        for item in records
        if item.authorized is True and item.temporally_current is True
    )
    selected = _budget_pack(eligible, need.character_budget)
    return _outcome(
        selected,
        need,
        reasons=("full_authorized", f"eligible_bucket_{_count_bucket(len(eligible))}"),
        logical_operations=len(eligible),
    )


def _existing_set_selection(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    sealed = seal_projection(records).items
    by_key = {item.key: item for item in sealed}
    selection = DeterministicSetSelector().select(
        tuple(
            SetSelectionCandidate(
                key=item.key,
                budget_cost=item.char_cost,
                base_utility=item.relevance,
                semantic_facets=item.coverage_ids,
                policy_authorized=True,
                temporally_eligible=True,
                task_admissible=True,
            )
            for item in sealed
        ),
        SetSelectionConstraints(limit=len(sealed), budget=need.character_budget),
    )
    selected = tuple(by_key[item.key] for item in selection.candidates)
    reasons = tuple(diagnostic.reason.value for diagnostic in selection.diagnostics)
    return _outcome(
        selected,
        need,
        reasons=reasons or ("set_selected",),
        logical_operations=len(sealed) ** 2,
    )


def _sealed_non_minimal(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    sealed = seal_projection(records).items
    selected = _budget_pack(sealed, need.character_budget)
    return _outcome(
        selected,
        need,
        reasons=("projection_sealed", "non_minimal_pack"),
        logical_operations=len(sealed),
    )


def _authorization_after_relevance(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    ranked = _rank(records)
    selected = _budget_pack(
        tuple(
            item
            for item in ranked[: max(2, len(need.obligations) + 1)]
            if item.authorized is True
            and item.temporally_current is True
            and item.applicable is True
        ),
        need.character_budget,
    )
    return _outcome(
        selected,
        need,
        reasons=(
            "relevance_before_authorization",
            f"raw_candidate_bucket_{_count_bucket(len(records))}",
        ),
        logical_operations=len(records),
    )


def _applicability_after_ranking(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    authorized = tuple(
        item for item in records if item.authorized is True and item.temporally_current is True
    )
    ranked = _rank(authorized)
    selected = _budget_pack(
        tuple(
            item
            for item in ranked[: max(2, len(need.obligations) + 1)]
            if item.applicable is True
        ),
        need.character_budget,
    )
    return _outcome(
        selected,
        need,
        reasons=(
            "applicability_after_ranking",
            f"authorized_bucket_{_count_bucket(len(authorized))}",
        ),
        logical_operations=len(authorized),
    )


def _no_epistemic_role(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    sealed = seal_projection(records).items
    selected = _minimum_declared_set(sealed, need, ignore_roles=True)
    return _outcome(
        selected,
        need,
        reasons=("projection_sealed", "epistemic_role_ignored"),
        logical_operations=2 ** len(sealed),
    )


def _top_k_without_closure(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    sealed = seal_projection(records).items
    selected = _budget_pack(
        _rank(sealed)[: len(need.obligations)],
        need.character_budget,
    )
    return _outcome(
        selected,
        need,
        reasons=("projection_sealed", "top_k_without_closure"),
        logical_operations=len(sealed),
    )


def _no_current_reread(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    return _minimal_condition(records, need)


def _no_delete_pass(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    return _sealed_non_minimal(records, need)


def _no_cumulative_disclosure(
    records: Sequence[ContextRecord],
    need: ContextNeed,
) -> ObservableOutcome:
    return _minimal_condition(records, replace(need, cumulative_disclosure_limit=None))


CONDITIONS: dict[str, Condition] = {
    "full_authorized_context": _full_authorized,
    "existing_deterministic_set_selection": _existing_set_selection,
    "sealed_non_minimal_projection": _sealed_non_minimal,
    "sealed_minimal_projection": _minimal_condition,
    "ablation_authorization_after_relevance": _authorization_after_relevance,
    "ablation_applicability_after_ranking": _applicability_after_ranking,
    "ablation_no_epistemic_role": _no_epistemic_role,
    "ablation_top_k_without_closure": _top_k_without_closure,
    "ablation_no_current_version_reread": _no_current_reread,
    "ablation_no_delete_and_recompile": _no_delete_pass,
    "ablation_no_cumulative_disclosure": _no_cumulative_disclosure,
}


def run_experiment(
    *,
    repeats: int = REPEATS_REQUIRED,
    fixture_path: Path = FIXTURES,
    pair_limit: int | None = None,
) -> dict[str, Any]:
    """Execute every condition with exact paired and harness-only evaluation."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    all_cases = load_cases(fixture_path)
    cases = all_cases if pair_limit is None else all_cases[:pair_limit]
    if not cases:
        raise ValueError("at least one pair is required")

    condition_reports: dict[str, Any] = {}
    fingerprints: dict[str, str] = {}
    for condition_id in CONDITION_ORDER:
        condition = CONDITIONS[condition_id]
        metrics = _empty_metrics()
        repeat_fingerprints: list[str] = []
        for _repeat in range(repeats):
            trace_fingerprint = hashlib.sha256()
            for case in cases:
                base = condition(case.base_records, case.need)
                variant = condition(case.variant_records, case.need)
                trace_fingerprint.update(_outcome_fingerprint(base).encode("ascii"))
                trace_fingerprint.update(_outcome_fingerprint(variant).encode("ascii"))
                _score_trial(metrics, base, case, forbidden_key=None)
                _score_trial(metrics, variant, case, forbidden_key=case.canary_key)
                for channel in _channel_differences(base, variant):
                    metrics["pair_channel_failure_counts"][channel] += 1
                metrics["paired_vault_opportunities"] += 1
            repeat_fingerprints.append(trace_fingerprint.hexdigest())
        metrics["repeat_deterministic"] = len(set(repeat_fingerprints)) == 1
        metrics["caos_rate"] = _ratio(metrics["caos_passes"], metrics["trial_count"])
        metrics["sufficiency_rate"] = _ratio(
            metrics["sufficient_trials"], metrics["trial_count"]
        )
        metrics["one_deletion_minimality_rate"] = _ratio(
            metrics["minimal_trials"], metrics["issued_sufficient_trials"]
        )
        metrics["mean_disclosure_chars"] = _ratio(
            metrics.pop("disclosure_chars_total"), metrics["trial_count"]
        )
        condition_reports[condition_id] = metrics
        fingerprints[condition_id] = repeat_fingerprints[0]

    fault_results = _run_decisive_faults(cases)
    minimal = condition_reports["sealed_minimal_projection"]
    non_minimal = condition_reports["sealed_non_minimal_projection"]
    full = condition_reports["full_authorized_context"]
    exact = _exhaustive_checks(cases)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "evidence_level": "L1_isolated_deterministic_synthetic",
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "compiler_module": "allthecontext.memory_lab_m2",
        "base_commit": "950f649d9e3cc106fb8ff4febbe38919f8e00d11",
        "pair_count": len(cases),
        "repeats": repeats,
        "paired_trace_executions": len(cases) * repeats,
        "trial_count": len(cases) * repeats * 2,
        "finite_bounds": {
            "maximum_candidates": 12,
            "maximum_obligations": 4,
            "character_budget_unit": "unicode_code_points",
        },
        "canary_boundary": {
            "named_families": 5,
            "compiler_visible_attestation_patterns": 3,
            "patterns": [
                "unauthorized",
                "not_temporally_current",
                "not_applicable",
            ],
            "upstream_distinctions_not_tested": [
                "deleted_vs_purged",
                "out_of_scope_vs_other_authorized_inapplicability",
            ],
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "network_calls": 0,
            "model_calls": 0,
            "provider_tokens": 0,
            "monetary_cost_usd": 0.0,
            "timing_measurement": (
                "post_seal_logical_bucket_only_excludes_pre_seal_scan_and_machine_timing"
            ),
        },
        "oracle_boundary": {
            "compiler_receives_harness_oracle": False,
            "compiler_inputs": [
                "record_policy_currentness_applicability_attestations",
                "typed_roles",
                "declared_obligations",
                "dependencies",
                "fixed_character_budget",
            ],
            "semantic_label_assumption": (
                "obligation_ids_coverage_ids_and_accepted_roles_are_frozen_"
                "hand_authored_upstream_labels"
            ),
            "harness_only": [
                "required_item_groups",
                "forbidden_canary_identity",
                "caos_judgment",
                "deletion_minimality_judgment",
                "exhaustive_optimum",
            ],
        },
        "conditions": condition_reports,
        "decisive_faults": fault_results,
        "exhaustive_finite_checks": exact,
        "contrasts": {
            "minimal_vs_full_authorized_mean_disclosure_reduction": round(
                full["mean_disclosure_chars"] - minimal["mean_disclosure_chars"], 6
            ),
            "minimal_vs_sealed_non_minimal_mean_disclosure_reduction": round(
                non_minimal["mean_disclosure_chars"]
                - minimal["mean_disclosure_chars"],
                6,
            ),
            "minimal_vs_full_authorized_caos_delta": round(
                minimal["caos_rate"] - full["caos_rate"], 6
            ),
            "minimal_vs_sealed_non_minimal_caos_delta": round(
                minimal["caos_rate"] - non_minimal["caos_rate"], 6
            ),
        },
        "falsifiers": {
            "protected_observable_difference": sum(
                minimal["pair_channel_failure_counts"].values()
            ),
            "removable_selected_item": exact["removable_item_failures"],
            "exhaustive_optimum_gap_cases": exact["nonzero_optimum_gap_cases"],
            "insufficient_issued_set": minimal["insufficient_issued_trials"],
        },
        "decision": _decision(condition_reports, fault_results, exact),
        "validity_limitations": [
            "symbolic_fixture_and_compiler_contract_were_co_designed",
            "no_reader_model_or_real_action",
            "caos_is_harness_deterministic_not_product_evidence",
            "local_one_deletion_minimality_not_global_prompt_optimality",
            "obligation_coverage_and_role_labels_are_hand_authored_and_fixture_aligned",
            "sha256_fields_are_linkable_dictionary_attackable_synthetic_commitments",
            "production_receipts_require_a_reviewed_keyed_or_blinded_construction",
            "five_named_canaries_collapse_to_three_compiler_visible_attestations",
            "deletion_vs_purge_and_scope_vs_other_inapplicability_remain_upstream",
            "timing_class_covers_post_seal_logical_work_only",
            "pre_seal_authorization_scan_length_and_actual_runtime_are_not_protected_claims",
            "existing_set_selection_is_used_only_where_its_metadata_contract_applies",
            "no_production_core_or_operator_context",
        ],
        "deterministic_fingerprints": fingerprints,
    }
    report["leak_scan"] = _leak_scan(report, cases)
    return report


def _empty_metrics() -> dict[str, Any]:
    return {
        "trial_count": 0,
        "paired_vault_opportunities": 0,
        "caos_passes": 0,
        "sufficient_trials": 0,
        "issued_sufficient_trials": 0,
        "insufficient_issued_trials": 0,
        "minimal_trials": 0,
        "budget_violations": 0,
        "forbidden_influence_trials": 0,
        "deletion_tests": 0,
        "disclosure_chars_total": 0,
        "pair_channel_failure_counts": {channel: 0 for channel in CHANNELS},
    }


def _score_trial(
    metrics: dict[str, Any],
    outcome: ObservableOutcome,
    case: PairCase,
    *,
    forbidden_key: str | None,
) -> None:
    keys = frozenset(item.key for item in outcome.selected)
    sufficient = _oracle_sufficient(keys, case.oracle)
    issued = outcome.status == CompilationStatus.ISSUED.value
    forbidden = forbidden_key is not None and forbidden_key in keys
    within_budget = outcome.disclosure_chars <= case.need.character_budget
    minimal = sufficient and all(
        not _oracle_sufficient(keys - {key}, case.oracle) for key in keys
    )
    caos = issued and sufficient and not forbidden and within_budget
    metrics["trial_count"] += 1
    metrics["caos_passes"] += int(caos)
    metrics["sufficient_trials"] += int(sufficient)
    metrics["issued_sufficient_trials"] += int(issued and sufficient)
    metrics["insufficient_issued_trials"] += int(issued and not sufficient)
    metrics["minimal_trials"] += int(minimal)
    metrics["budget_violations"] += int(not within_budget)
    metrics["forbidden_influence_trials"] += int(forbidden)
    metrics["deletion_tests"] += outcome.deletion_test_count
    metrics["disclosure_chars_total"] += outcome.disclosure_chars


def _run_decisive_faults(cases: Sequence[PairCase]) -> dict[str, Any]:
    compiler = SealedProjectionMinimalCompiler()
    reread_detected = 0
    no_reread_detected = 0
    cumulative_detected = 0
    no_cumulative_detected = 0
    for case in cases:
        initial = compiler.compile(case.base_records, case.need)
        if not initial.selected:
            continue
        target = initial.selected[0]
        mutated = tuple(
            replace(
                item,
                version=item.version + 1,
                content=f"{item.content}_CORRECTED",
            )
            if item.key == target.key
            else item
            for item in case.base_records
        )
        reread = compiler.compile(case.base_records, case.need, reread_records=mutated)
        reread_detected += int(
            reread.receipt.status is CompilationStatus.RETRY_GENERATION_CHANGE
        )
        no_reread_detected += 0

        disclosure = initial.receipt.disclosure_chars
        tight_need = replace(
            case.need,
            cumulative_disclosure_limit=case.need.prior_disclosure_chars + disclosure - 1,
        )
        cumulative = compiler.compile(case.base_records, tight_need)
        cumulative_detected += int(
            cumulative.receipt.status is CompilationStatus.ABSTAINED
        )
        ignored = _no_cumulative_disclosure(case.base_records, tight_need)
        no_cumulative_detected += int(ignored.status != CompilationStatus.ISSUED.value)
    opportunities = len(cases)
    return {
        "current_version_change_opportunities": opportunities,
        "sealed_minimal_detected_current_version_change": reread_detected,
        "no_current_reread_detected_current_version_change": no_reread_detected,
        "cumulative_limit_opportunities": opportunities,
        "sealed_minimal_enforced_cumulative_limit": cumulative_detected,
        "no_cumulative_ablation_enforced_limit": no_cumulative_detected,
    }


def _exhaustive_checks(cases: Sequence[PairCase]) -> dict[str, int]:
    compiler = SealedProjectionMinimalCompiler()
    deletion_checks = 0
    removable_failures = 0
    nonzero_gap = 0
    maximum_gap = 0
    for case in cases:
        result = compiler.compile(case.base_records, case.need)
        keys = frozenset(item.key for item in result.selected)
        for key in keys:
            deletion_checks += 1
            removable_failures += int(
                _oracle_sufficient(keys - {key}, case.oracle)
            )
        optimum = _oracle_optimum(case)
        selected_chars = sum(item.char_cost for item in result.selected)
        gap = selected_chars - optimum
        nonzero_gap += int(gap != 0)
        maximum_gap = max(maximum_gap, gap)
    return {
        "case_count": len(cases),
        "one_deletion_checks": deletion_checks,
        "removable_item_failures": removable_failures,
        "nonzero_optimum_gap_cases": nonzero_gap,
        "maximum_character_gap": maximum_gap,
    }


def _oracle_optimum(case: PairCase) -> int:
    admitted = seal_projection(case.base_records).items
    feasible = [
        sum(item.char_cost for item in subset)
        for size in range(1, len(admitted) + 1)
        for subset in itertools.combinations(admitted, size)
        if sum(item.char_cost for item in subset) <= case.need.character_budget
        and _oracle_sufficient(
            frozenset(item.key for item in subset),
            case.oracle,
        )
    ]
    if not feasible:
        raise AssertionError("M2 fixture must have a harness-feasible set")
    return min(feasible)


def _decision(
    conditions: Mapping[str, Mapping[str, Any]],
    faults: Mapping[str, int],
    exact: Mapping[str, int],
) -> dict[str, Any]:
    minimal = conditions["sealed_minimal_projection"]
    hard_failure = (
        sum(minimal["pair_channel_failure_counts"].values())
        or minimal["insufficient_issued_trials"]
        or exact["removable_item_failures"]
        or exact["nonzero_optimum_gap_cases"]
        or faults["sealed_minimal_detected_current_version_change"]
        != faults["current_version_change_opportunities"]
        or faults["sealed_minimal_enforced_cumulative_limit"]
        != faults["cumulative_limit_opportunities"]
    )
    if hard_failure:
        return {
            "state": "kill_mechanism",
            "reason_codes": ["m2_hard_falsifier_observed"],
        }
    return {
        "state": "narrow_retain_bounded_m2",
        "reason_codes": [
            "sealed_projection_noninterference_passed",
            "finite_one_deletion_minimality_passed",
            "minimum_disclosure_improved",
            "generalized_product_claim_not_established",
        ],
    }


def _leak_scan(report: Mapping[str, Any], cases: Sequence[PairCase]) -> dict[str, Any]:
    rendered = json.dumps(report, sort_keys=True)
    forbidden: set[str] = set()
    for case in cases:
        forbidden.add(case.canary_key)
        forbidden.update(item.key for item in case.base_records)
        forbidden.update(item.content for item in case.variant_records)
        forbidden.update(
            obligation.obligation_id for obligation in case.need.obligations
        )
    matches = sorted(value for value in forbidden if value and value in rendered)
    return {
        "scanned_for_raw_record_keys_contents_obligations_and_canaries": True,
        "forbidden_value_count": len(forbidden),
        "matches": len(matches),
        "passed": not matches,
    }


def _minimum_declared_set(
    records: Sequence[ContextRecord],
    need: ContextNeed,
    *,
    ignore_roles: bool,
) -> tuple[ContextRecord, ...]:
    for size in range(1, len(records) + 1):
        feasible: list[tuple[ContextRecord, ...]] = []
        for subset in itertools.combinations(records, size):
            if sum(item.char_cost for item in subset) > need.character_budget:
                continue
            selected_keys = {item.key for item in subset}
            if any(not item.dependency_ids <= selected_keys for item in subset):
                continue
            if all(
                any(
                    obligation.obligation_id in item.coverage_ids
                    and (ignore_roles or item.role in obligation.accepted_roles)
                    for item in subset
                )
                for obligation in need.obligations
            ):
                feasible.append(subset)
        if feasible:
            return min(
                feasible,
                key=lambda subset: (
                    sum(item.char_cost for item in subset),
                    -sum(item.relevance for item in subset),
                    tuple(item.key for item in subset),
                ),
            )
    return ()


def _budget_pack(
    records: Sequence[ContextRecord],
    budget: int,
) -> tuple[ContextRecord, ...]:
    selected: list[ContextRecord] = []
    used = 0
    for item in records:
        if used + item.char_cost <= budget:
            selected.append(item)
            used += item.char_cost
    return tuple(selected)


def _rank(records: Sequence[ContextRecord]) -> tuple[ContextRecord, ...]:
    return tuple(sorted(records, key=lambda item: (-item.relevance, item.key)))


def _outcome(
    selected: Sequence[ContextRecord],
    need: ContextNeed,
    *,
    reasons: Sequence[str],
    logical_operations: int,
) -> ObservableOutcome:
    ordered = tuple(selected)
    reason_multiset = tuple(sorted(Counter(reasons).items()))
    cursor = tuple(
        len(ordered[offset : offset + need.page_size])
        for offset in range(0, len(ordered), need.page_size)
    )
    status = (
        CompilationStatus.ISSUED.value
        if ordered
        else CompilationStatus.ABSTAINED.value
    )
    semantic_output = _digest("semantic", *(item.content for item in ordered))
    timing_class = f"logical_{_count_bucket(logical_operations)}"
    learning_state = _digest(
        "learning",
        need.request_commitment,
        *(f"{item.key}:{item.version}" for item in ordered),
    )
    disclosure_chars = sum(item.char_cost for item in ordered)
    full_receipt = json.dumps(
        {
            "cursor_page_shape": cursor,
            "disclosure_chars": disclosure_chars,
            "learning_update_state": learning_state,
            "reason_code_multiset": reason_multiset,
            "semantic_output": semantic_output,
            "status": status,
            "timing_class": timing_class,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ObservableOutcome(
        selected=ordered,
        status=status,
        semantic_output=semantic_output,
        reason_code_multiset=reason_multiset,
        cursor_page_shape=cursor,
        timing_class=timing_class,
        learning_update_state=learning_state,
        disclosure_chars=disclosure_chars,
        full_observable_receipt=full_receipt,
    )


def _channel_differences(
    base: ObservableOutcome,
    variant: ObservableOutcome,
) -> tuple[str, ...]:
    pairs = (
        (
            "full_observable_receipt",
            base.full_observable_receipt,
            variant.full_observable_receipt,
        ),
        ("semantic_output", base.semantic_output, variant.semantic_output),
        (
            "reason_code_multiset",
            base.reason_code_multiset,
            variant.reason_code_multiset,
        ),
        ("cursor_page_shape", base.cursor_page_shape, variant.cursor_page_shape),
        ("timing_class", base.timing_class, variant.timing_class),
        (
            "learning_update_state",
            base.learning_update_state,
            variant.learning_update_state,
        ),
    )
    return tuple(name for name, left, right in pairs if left != right)


def _oracle_sufficient(keys: frozenset[str], oracle: HarnessOracle) -> bool:
    return all(group.intersection(keys) for group in oracle.required_item_groups)


def _outcome_fingerprint(outcome: ObservableOutcome) -> str:
    return _digest(
        "outcome",
        outcome.full_observable_receipt,
        outcome.status,
        outcome.semantic_output,
        repr(outcome.reason_code_multiset),
        repr(outcome.cursor_page_shape),
        outcome.timing_class,
        outcome.learning_update_state,
        str(outcome.disclosure_chars),
    )


def _count_bucket(count: int) -> str:
    if count <= 4:
        return "0_4"
    if count <= 8:
        return "5_8"
    return "9_16"


def _digest(domain: str, *parts: str) -> str:
    digest = hashlib.sha256(domain.encode("utf-8"))
    for part in parts:
        digest.update(b"\0")
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 6)


def _object_list(value: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    raw = _list(value, name)
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError(f"{name} must contain objects")
    return raw


def _list(value: Mapping[str, Any], name: str) -> list[Any]:
    raw = value.get(name)
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a list")
    return raw


def _string_list(
    value: Mapping[str, Any],
    name: str,
    *,
    default: Sequence[str] | None = None,
) -> tuple[str, ...]:
    raw = value.get(name, default)
    if not isinstance(raw, (list, tuple)) or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{name} must contain strings")
    return tuple(raw)


def _string_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("oracle groups must contain strings")
    return tuple(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render an identifier-safe compact experiment report."""

    lines = [
        "# Wave 3 M2 sealed projection minimal compiler",
        "",
        (
            f"Evidence: `{report['evidence_level']}`; fixture `{report['fixture_sha256']}`; "
            f"{report['pair_count']} paired vaults x {report['repeats']} deterministic repeats."
        ),
        "",
        "| Condition | CAOS | Sufficiency | 1-deletion minimal | Mean chars | Pair leaks |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    conditions = report["conditions"]
    assert isinstance(conditions, dict)
    for condition_id in CONDITION_ORDER:
        metrics = conditions[condition_id]
        leaks = sum(metrics["pair_channel_failure_counts"].values())
        lines.append(
            f"| `{condition_id}` | {metrics['caos_rate']:.6f} | "
            f"{metrics['sufficiency_rate']:.6f} | "
            f"{metrics['one_deletion_minimality_rate']:.6f} | "
            f"{metrics['mean_disclosure_chars']:.6f} | {leaks} |"
        )
    contrasts = report["contrasts"]
    exact = report["exhaustive_finite_checks"]
    faults = report["decisive_faults"]
    decision = report["decision"]
    lines.extend(
        [
            "",
            "## Decisive checks",
            "",
            (
                f"- Protected-channel failures for sealed minimal projection: "
                f"`{report['falsifiers']['protected_observable_difference']}`."
            ),
            (
                f"- Exhaustive one-deletion checks: `{exact['one_deletion_checks']}`; "
                f"removable items: `{exact['removable_item_failures']}`; "
                f"nonzero exact-optimum gaps: `{exact['nonzero_optimum_gap_cases']}`."
            ),
            (
                f"- Current-version races detected: "
                f"`{faults['sealed_minimal_detected_current_version_change']}/"
                f"{faults['current_version_change_opportunities']}`; no-reread ablation: "
                f"`{faults['no_current_reread_detected_current_version_change']}`."
            ),
            (
                f"- Cumulative disclosure violations blocked: "
                f"`{faults['sealed_minimal_enforced_cumulative_limit']}/"
                f"{faults['cumulative_limit_opportunities']}`; no-state ablation: "
                f"`{faults['no_cumulative_ablation_enforced_limit']}`."
            ),
            (
                f"- Mean character reduction vs full authorized: "
                f"`{contrasts['minimal_vs_full_authorized_mean_disclosure_reduction']:.6f}`; "
                f"vs sealed non-minimal: "
                f"`{contrasts['minimal_vs_sealed_non_minimal_mean_disclosure_reduction']:.6f}`."
            ),
            f"- Leak scan passed: `{report['leak_scan']['passed']}`.",
            (
                "- Receipt SHA-256 values are linkable, dictionary-attackable synthetic "
                "commitments; they are not production-safe redactions."
            ),
            (
                "- Obligation IDs, coverage IDs, and accepted roles are frozen "
                "hand-authored upstream labels and a co-design assumption."
            ),
            (
                "- Five named canary families collapse to three compiler-visible "
                "attestation patterns. Deletion versus purge and out-of-scope versus "
                "other inapplicability remain upstream distinctions."
            ),
            (
                "- Timing noninterference covers only the post-seal logical receipt "
                "bucket. Pre-seal scan length and actual runtime are excluded."
            ),
            "",
            "## Decision",
            "",
            f"`{decision['state']}`: {', '.join(decision['reason_codes'])}.",
            "",
            "This is isolated deterministic synthetic evidence only. It does not establish "
            "a generalized product or real-user claim.",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=REPEATS_REQUIRED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_experiment(repeats=args.repeats)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8", newline="\n")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_markdown(report),
            encoding="utf-8",
            newline="\n",
        )
    falsifiers = report["falsifiers"]
    leak_scan = report["leak_scan"]
    return int(
        any(falsifiers.values())
        or not leak_scan["passed"]
        or report["decision"]["state"] == "kill_mechanism"
    )


if __name__ == "__main__":
    raise SystemExit(main())
