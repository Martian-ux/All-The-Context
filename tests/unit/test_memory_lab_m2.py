from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

from allthecontext.memory_lab_m2 import (
    CompilationReason,
    CompilationStatus,
    ContextNeed,
    ContextObligation,
    ContextRecord,
    ContextRole,
    SealedProjectionMinimalCompiler,
    receipt_observables,
    serialize_receipt,
)

from bench.memory_lab_m2 import (
    _exhaustive_checks,
    load_cases,
    run_experiment,
)


def _need(*, budget: int = 100) -> ContextNeed:
    return ContextNeed(
        request_commitment="synthetic-request",
        obligations=(
            ContextObligation(
                "declared-obligation",
                frozenset({ContextRole.CURRENT_CLAIM}),
            ),
        ),
        character_budget=budget,
    )


def _record(
    *,
    key: str = "record-a",
    content: str = "CURRENT_VALUE",
    covers: bool = True,
) -> ContextRecord:
    return ContextRecord(
        key=key,
        version=1,
        content=content,
        role=ContextRole.CURRENT_CLAIM,
        coverage_ids=(
            frozenset({"declared-obligation"}) if covers else frozenset()
        ),
        relevance=10,
        authorized=True,
        temporally_current=True,
        applicable=True,
    )


def test_fixture_declares_bounded_finite_thousand_pair_matrix() -> None:
    cases = load_cases()

    assert len(cases) == 1000
    assert max(len(case.variant_records) for case in cases) <= 12
    assert max(len(case.need.obligations) for case in cases) <= 4
    assert len({case.canary_key for case in cases}) == 1000


def test_each_canary_family_leaves_every_receipt_field_identical() -> None:
    cases = load_cases()
    compiler = SealedProjectionMinimalCompiler()

    # The first template expands in five contiguous families of twenty variants.
    for case in (cases[0], cases[20], cases[40], cases[60], cases[80]):
        base = compiler.compile(case.base_records, case.need).receipt
        variant = compiler.compile(case.variant_records, case.need).receipt

        assert receipt_observables(base) == receipt_observables(variant)
        assert serialize_receipt(base) == serialize_receipt(variant)


def test_semantic_and_budget_infeasibility_have_distinct_terminal_reasons() -> None:
    compiler = SealedProjectionMinimalCompiler()
    over_budget = compiler.compile((_record(content="TOO_LONG"),), _need(budget=3))
    semantically_infeasible = compiler.compile(
        (_record(covers=False),),
        _need(budget=100),
    )

    assert over_budget.receipt.status is CompilationStatus.ABSTAINED
    assert over_budget.receipt.reason_code_multiset == (
        (CompilationReason.BUDGET_INFEASIBLE.value, 1),
    )
    assert semantically_infeasible.receipt.status is CompilationStatus.ABSTAINED
    assert semantically_infeasible.receipt.reason_code_multiset == (
        (CompilationReason.OBLIGATION_INFEASIBLE.value, 1),
    )


def test_budget_search_can_choose_more_short_items_than_semantic_minimum() -> None:
    combined = ContextRecord(
        key="combined",
        version=1,
        content="X" * 30,
        role=ContextRole.CURRENT_CLAIM,
        coverage_ids=frozenset({"first", "second"}),
        relevance=100,
        authorized=True,
        temporally_current=True,
        applicable=True,
    )
    first = replace(
        combined,
        key="first",
        content="A" * 8,
        coverage_ids=frozenset({"first"}),
    )
    second = replace(
        combined,
        key="second",
        content="B" * 8,
        coverage_ids=frozenset({"second"}),
    )
    need = ContextNeed(
        request_commitment="two-obligations",
        obligations=(
            ContextObligation("first", frozenset({ContextRole.CURRENT_CLAIM})),
            ContextObligation("second", frozenset({ContextRole.CURRENT_CLAIM})),
        ),
        character_budget=20,
    )

    result = SealedProjectionMinimalCompiler().compile(
        (combined, first, second),
        need,
    )

    assert result.receipt.status is CompilationStatus.ISSUED
    assert {item.key for item in result.selected} == {"first", "second"}


def test_receipt_is_complete_but_contains_only_synthetic_commitments() -> None:
    result = SealedProjectionMinimalCompiler().compile((_record(),), _need())
    rendered = serialize_receipt(result.receipt)
    parsed = json.loads(rendered)

    assert set(parsed) == {
        "cursor_page_shape",
        "deletion_tests",
        "disclosure_chars",
        "learning_state_digest",
        "reason_code_multiset",
        "sealed_projection_commitment",
        "selected_item_commitments",
        "semantic_output_digest",
        "status",
        "timing_class",
    }
    assert "record-a" not in rendered
    assert "CURRENT_VALUE" not in rendered
    assert "declared-obligation" not in rendered


def test_current_version_reread_retries_after_correction() -> None:
    record = _record()
    corrected = replace(record, version=2, content="CORRECTED_VALUE")

    result = SealedProjectionMinimalCompiler().compile(
        (record,),
        _need(),
        reread_records=(corrected,),
    )

    assert result.receipt.status is CompilationStatus.RETRY_GENERATION_CHANGE
    assert result.receipt.reason_code_multiset == (
        (CompilationReason.GENERATION_CHANGED.value, 1),
    )


def test_compiler_api_cannot_receive_harness_oracle() -> None:
    parameters = inspect.signature(SealedProjectionMinimalCompiler.compile).parameters

    assert set(parameters) == {"self", "records", "need", "reread_records"}


def test_small_run_exposes_decisive_ablation_regressions() -> None:
    report = run_experiment(repeats=2, pair_limit=100)
    conditions = report["conditions"]
    minimal = conditions["sealed_minimal_projection"]

    assert report["leak_scan"]["passed"] is True
    assert sum(minimal["pair_channel_failure_counts"].values()) == 0
    assert minimal["caos_rate"] == 1.0
    assert minimal["one_deletion_minimality_rate"] == 1.0
    assert (
        sum(
            conditions["ablation_authorization_after_relevance"][
                "pair_channel_failure_counts"
            ].values()
        )
        > 0
    )
    assert (
        conditions["ablation_no_delete_and_recompile"][
            "one_deletion_minimality_rate"
        ]
        < 1.0
    )
    assert (
        report["decisive_faults"]["sealed_minimal_detected_current_version_change"]
        == 100
    )
    assert (
        report["decisive_faults"]["no_current_reread_detected_current_version_change"]
        == 0
    )


def test_exhaustive_harness_confirms_every_selected_deletion() -> None:
    exact = _exhaustive_checks(load_cases()[:100])

    assert exact["case_count"] == 100
    assert exact["one_deletion_checks"] > 0
    assert exact["removable_item_failures"] == 0
    assert exact["nonzero_optimum_gap_cases"] == 0


def test_checked_in_report_preserves_twenty_repeat_result_and_limits() -> None:
    path = Path(__file__).parents[2] / "bench" / "reports" / "memory_lab_m2_wave3.json"
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["schema"] == "atc.memory-lab.m2-report.v1"
    assert report["pair_count"] == 1000
    assert report["repeats"] == 20
    assert report["paired_trace_executions"] == 20_000
    assert report["falsifiers"] == {
        "exhaustive_optimum_gap_cases": 0,
        "insufficient_issued_set": 0,
        "protected_observable_difference": 0,
        "removable_selected_item": 0,
    }
    assert report["leak_scan"]["passed"] is True
    assert (
        "sha256_fields_are_linkable_dictionary_attackable_synthetic_commitments"
        in report["validity_limitations"]
    )
    assert (
        report["oracle_boundary"]["semantic_label_assumption"]
        == "obligation_ids_coverage_ids_and_accepted_roles_are_frozen_"
        "hand_authored_upstream_labels"
    )
    assert report["canary_boundary"] == {
        "compiler_visible_attestation_patterns": 3,
        "named_families": 5,
        "patterns": [
            "unauthorized",
            "not_temporally_current",
            "not_applicable",
        ],
        "upstream_distinctions_not_tested": [
            "deleted_vs_purged",
            "out_of_scope_vs_other_authorized_inapplicability",
        ],
    }
    assert (
        report["environment"]["timing_measurement"]
        == "post_seal_logical_bucket_only_excludes_pre_seal_scan_and_machine_timing"
    )
