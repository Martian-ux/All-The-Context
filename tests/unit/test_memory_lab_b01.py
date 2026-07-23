from __future__ import annotations

import hashlib
import json
from pathlib import Path

from allthecontext.memory_lab import NoMemoryBaseline
from allthecontext.memory_lab_baselines import StableObservationLogBaseline
from allthecontext.memory_lab_programmatic_log import (
    InspectionReceipt,
    ProgrammaticInspectionLimits,
    ProgrammaticLogInspectionAdapter,
)

from bench.memory_lab import FIXTURES as M0_FIXTURES
from bench.memory_lab import AtcRetrievalAdapter
from bench.memory_lab_b01 import (
    CONDITION_ORDER,
    CONFIG,
    FIXTURES,
    FrozenProgrammaticAtcCombination,
    NativeProgrammaticCondition,
    RetrievalCondition,
    _execute_selected_context,
    _retrieval_task,
    assert_identifier_safe,
    load_fixture_bundle,
    render_markdown_report,
    run_fixture,
)

B01_FIXTURE_SHA256 = "9f8cf3185e30a4cdc9d0475f8fee643e1a141cf53d11658292c8934a01706d97"
B01_CONFIG_SHA256 = "42a27914ce494875ece97e1f700e512c17371f75d901d1f59603fd22b4b54d32"
M0_FIXTURE_SHA256 = "5601692ea305448f6b299c32725a93c73ca83ccee66f325e22cbcbedfa0cc68f"


def test_b01_fixture_and_config_are_frozen_sanitized_and_do_not_modify_m0() -> None:
    events, scenarios, config = load_fixture_bundle()

    assert hashlib.sha256(FIXTURES.read_bytes()).hexdigest() == B01_FIXTURE_SHA256
    assert hashlib.sha256(CONFIG.read_bytes()).hexdigest() == B01_CONFIG_SHA256
    assert hashlib.sha256(M0_FIXTURES.read_bytes()).hexdigest() == M0_FIXTURE_SHA256
    assert len(events) == 45
    assert len(scenarios) == 9
    assert config.repeats == 20
    assert {scenario.task.evaluated_at for scenario in scenarios} == {
        config.frozen_clock
    }
    assert {scenario.task.context_budget_chars for scenario in scenarios} == {
        config.context_budget_chars
    }
    assert {scenario.task.limit for scenario in scenarios} == {
        config.max_selected_events
    }
    assert 2 * len(events) <= config.max_events_scanned_per_operation
    assert all(
        not event.content_document().startswith(event.event_id) for event in events
    )


def test_adapter_facing_task_contains_no_oracle_or_forbidden_labels() -> None:
    _, scenarios, _ = load_fixture_bundle()

    for scenario in scenarios:
        adapter_task = _retrieval_task(scenario.task)
        assert adapter_task.evidence_groups == ()
        assert adapter_task.forbidden_ids == frozenset()
        if scenario.oracle.expected_action is not None:
            assert scenario.oracle.expected_action not in adapter_task.query
        assert not scenario.oracle.forbidden_ids.intersection(
            set(adapter_task.query.split())
        )


def test_confirmatory_values_reuse_the_frozen_supported_dsl_grammar() -> None:
    _, scenarios, _ = load_fixture_bundle()

    def grammar_signature(strategy: str, partition: str) -> set[tuple[object, ...]]:
        return {
            (
                scenario.task.descriptor.strategy,
                tuple(key for key, _ in scenario.task.descriptor.selectors),
                scenario.task.descriptor.observation_event_type,
                scenario.task.descriptor.policy_event_type,
                scenario.task.descriptor.stage,
                scenario.task.descriptor.outcome_field,
                scenario.task.descriptor.policy_match_field,
                scenario.task.descriptor.action_field,
                scenario.task.descriptor.threshold_field,
                    scenario.task.descriptor.window is not None,
                scenario.task.descriptor.trigger_value is not None,
            )
            for scenario in scenarios
            if scenario.partition == partition
            and scenario.task.descriptor.strategy == strategy
        }

    assert grammar_signature("latest_route", "development") == grammar_signature(
        "latest_route", "confirmatory"
    )
    assert grammar_signature("threshold_route", "development") == grammar_signature(
        "threshold_route", "confirmatory"
    )


def test_programmatic_reader_issues_multiple_bounded_read_only_operations() -> None:
    events, scenarios, config = load_fixture_bundle()
    scenario = scenarios[0]
    adapter = ProgrammaticLogInspectionAdapter(config.programmatic_limits)

    adapter.prepare(events)
    try:
        receipt = adapter.inspect(scenario.task)
    finally:
        adapter.close()

    assert isinstance(receipt, InspectionReceipt)
    assert receipt.reason_code == "selected"
    assert 1 < len(receipt.operations) <= config.max_operations
    assert [operation.operation for operation in receipt.operations] == [
        "resolve_current_authorized_snapshot",
        "filter_observations",
        "take_latest_by_sequence",
        "join_policy_on_latest_outcome",
    ]
    assert all(
        operation.scanned_count <= config.max_events_scanned_per_operation
        for operation in receipt.operations
    )
    assert all(
        operation.result_count <= config.max_results_per_operation
        for operation in receipt.operations
    )
    selected_ids = tuple(item.object_id for item in receipt.items)
    assert _execute_selected_context(
        {event.event_id: event for event in events},
        scenario.task,
        selected_ids,
    ) == scenario.oracle.expected_action
    assert adapter.manifest.network_access is False
    assert adapter.manifest.writes_canonical_state is False


def test_programmatic_reader_rejects_before_over_cap_scan_or_materialization() -> None:
    events, scenarios, _ = load_fixture_bundle()
    two_events = events[:2]
    task = scenarios[0].task
    scan_limited = ProgrammaticLogInspectionAdapter(
        ProgrammaticInspectionLimits(
            max_operations=5,
            max_events_scanned_per_operation=3,
            max_results_per_operation=4,
        )
    )
    result_limited = ProgrammaticLogInspectionAdapter(
        ProgrammaticInspectionLimits(
            max_operations=5,
            max_events_scanned_per_operation=4,
            max_results_per_operation=1,
        )
    )

    scan_limited.prepare(two_events)
    try:
        scan_receipt = scan_limited.inspect(task)
    finally:
        scan_limited.close()
    result_limited.prepare(two_events)
    try:
        result_receipt = result_limited.inspect(task)
    finally:
        result_limited.close()

    assert scan_receipt.abstained
    assert scan_receipt.reason_code == "operation_budget_exhausted"
    assert scan_receipt.operations == ()
    assert result_receipt.abstained
    assert result_receipt.reason_code == "operation_budget_exhausted"
    assert result_receipt.operations == ()


def test_observation_without_matching_policy_abstains() -> None:
    events, scenarios, config = load_fixture_bundle()
    scenario = next(item for item in scenarios if item.task.task_id == "task-confirm-n")
    adapter = ProgrammaticLogInspectionAdapter(config.programmatic_limits)

    adapter.prepare(events)
    try:
        receipt = adapter.inspect(scenario.task)
    finally:
        adapter.close()

    assert receipt.abstained
    assert receipt.items == ()
    assert receipt.reason_code == "no_matching_route"
    assert receipt.operations[-1].operation == "join_policy_on_latest_outcome"
    assert receipt.operations[-1].result_count == 0


def test_same_selector_wrong_scope_decoy_is_excluded_by_every_condition(
    tmp_path: Path,
) -> None:
    events, scenarios, config = load_fixture_bundle()
    scenario = next(item for item in scenarios if item.task.task_id == "task-confirm-b")
    decoy_id = "evt-b-scope-decoy"
    decoy = next(event for event in events if event.event_id == decoy_id)
    conditions = (
        RetrievalCondition(
            NoMemoryBaseline(),
            operation_name="no_memory",
            count_operation=False,
        ),
        RetrievalCondition(
            StableObservationLogBaseline(config.context_budget_chars),
            operation_name="stable_current_state_lexical_search",
        ),
        NativeProgrammaticCondition(config.programmatic_limits),
        RetrievalCondition(
            AtcRetrievalAdapter(
                tmp_path / "atc",
                context_budget_chars=config.context_budget_chars,
            ),
            operation_name="atc_retrieval_v3",
        ),
        FrozenProgrammaticAtcCombination(tmp_path / "combination", config),
    )

    assert decoy.fields["project"] == dict(scenario.task.descriptor.selectors)["project"]
    assert decoy.fields["subject"] == dict(scenario.task.descriptor.selectors)["subject"]
    assert decoy.event_type == scenario.task.descriptor.policy_event_type
    assert decoy.fields["when_outcome"] == "stalled"
    assert decoy.fields["action"] == "act-plausible-decoy"
    assert not set(scenario.task.scopes).intersection(decoy.scopes)
    assert decoy_id in scenario.oracle.forbidden_ids

    for condition in conditions:
        condition.prepare(events)
        try:
            receipt = condition.inspect(scenario.task)
        finally:
            condition.close()
        assert decoy_id not in {item.object_id for item in receipt.items}


def test_b01_twenty_repeat_result_is_deterministic_identifier_safe_and_bounded(
    tmp_path: Path,
) -> None:
    report = run_fixture(tmp_path)

    assert report["fixture_sha256"] == B01_FIXTURE_SHA256
    assert report["config_sha256"] == B01_CONFIG_SHA256
    assert report["repeats"] == 20
    assert tuple(report["conditions"]) == CONDITION_ORDER
    assert report["boundary"] == {
        "operator_core_touched": False,
        "personal_context_used": False,
        "external_code_used": False,
        "network_service_used": False,
        "provider_or_model_used": False,
        "canonical_state_written": False,
        "oracle_crossed_adapter_boundary": False,
    }
    expected_confirmatory_caos = {
        "no-memory": 0.285714,
        "stable-observation-current-state": 0.428571,
        "bounded-programmatic-structured-log": 0.857143,
        "atc-retrieval-v3": 0.142857,
        "frozen-programmatic-atc-combination": 1.0,
    }
    for condition_id, expected_caos in expected_confirmatory_caos.items():
        condition = report["conditions"][condition_id]
        assert (
            condition["partitions"]["confirmatory"]["caos_rate"] == expected_caos
        )
        assert condition["metrics"]["deterministic_task_rate"] == 1.0
        assert condition["metrics"]["contract_violation_count"] == 0
        assert condition["metrics"]["context_budget_violation_count"] == 0
        assert condition["metrics"]["operation_budget_violation_count"] == 0
        assert condition["metrics"]["model_calls"] == 0
        assert condition["metrics"]["provider_tokens"] == 0
        assert condition["metrics"]["monetary_cost_usd"] == 0.0
        assert all(
            task["disclosure_chars"] <= report["budgets"]["context_budget_chars"]
            for task in condition["tasks"]
        )
        assert all(
            task["operation_count"] <= report["budgets"]["max_operations"]
            for task in condition["tasks"]
        )

    assert report["decision"] == {
        "state": "KILL_MECHANISM",
        "comparator": "stable-observation-current-state",
        "confirmatory_caos_gain": 0.428572,
        "confirmatory_operation_premium": 2.571429,
        "action_family_caos_gains": {
            "latest_route": 0.5,
            "threshold_route": 1.0,
        },
        "hard_gate_failures": [],
        "reason_codes": ["operation_premium_above_cap"],
        "operation_accounting": (
            "counted_dsl_read_operations_vs_one_top_level_lexical_adapter_call"
        ),
        "internal_condition_work_normalized": False,
        "kill_applies_only_to": (
            "this_bounded_hand_authored_dsl_configuration_under_external_operation_accounting"
        ),
        "comparative_compute_efficiency_established": False,
        "general_programmatic_inspection_falsified": False,
        "pro_long_falsified": False,
        "combination_confirmatory_caos": 1.0,
        "combination_disposition": (
            "NOT_PROMOTED_UNDER_SAME_B01_EXTERNAL_OPERATION_GATE"
        ),
        "scope": "isolated_synthetic_evidence_only_no_production_promotion",
    }
    assert report["identifier_leak_scan"]["passed"] is True
    assert (
        "frozen_hand_authored_dsl_not_arbitrary_program_synthesis"
        in report["validity_limitations"]
    )
    assert (
        "not_a_pro_long_reproduction_no_equivalent_agent_action_model"
        in report["validity_limitations"]
    )

    events, scenarios, _ = load_fixture_bundle()
    rendered_json = json.dumps(report, sort_keys=True)
    rendered_markdown = render_markdown_report(report)
    assert_identifier_safe(rendered_json, events, scenarios)
    assert_identifier_safe(rendered_markdown, events, scenarios)
    for event in events:
        assert event.event_id not in rendered_json
        assert event.content_document() not in rendered_json
    for scenario in scenarios:
        assert scenario.task.task_id not in rendered_json
        assert scenario.task.descriptor.lexical_query() not in rendered_json
        if scenario.oracle.expected_action is not None:
            assert scenario.oracle.expected_action not in rendered_json


def test_programmatic_module_contains_no_task_specific_fixture_ids() -> None:
    source = (
        Path(__file__).parents[2]
        / "packages"
        / "allthecontext"
        / "src"
        / "allthecontext"
        / "memory_lab_programmatic_log.py"
    ).read_text(encoding="utf-8")
    events, scenarios, _ = load_fixture_bundle()

    assert all(event.event_id not in source for event in events)
    assert all(scenario.task.task_id not in source for scenario in scenarios)
