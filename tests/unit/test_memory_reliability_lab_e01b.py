from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from bench.memory_reliability_lab_e01b import (
    COORDINATOR_BASE,
    FIXTURES,
    REFERENCE_FIXTURES,
    REPORT_SCHEMA,
    load_fixture,
    run_fixture,
)

FROZEN_REPORT = (
    Path(__file__).parents[2]
    / "bench"
    / "reports"
    / "memory_reliability_e01b_wave3.json"
)


def test_e01b_fixture_is_bounded_to_frozen_e01_and_coordinator_base() -> None:
    fixture, cases = load_fixture()

    assert fixture["coordinator_base"] == COORDINATOR_BASE
    assert fixture["reference_e01_fixture"]["sha256"] == hashlib.sha256(
        REFERENCE_FIXTURES.read_bytes()
    ).hexdigest()
    assert fixture["content_policy"] == {
        "synthetic": True,
        "symbolic_values_only": True,
        "real_personal_context": False,
        "external_code": False,
        "network_access": False,
        "operator_core_access": False,
    }
    assert len(cases) == 12
    assert {scenario for case in cases for scenario in case.reference_scenarios} == {
        "STATE_AUTHORITY_CURRENTNESS",
        "CORRECTION_INVALIDATES_DERIVED",
        "FORGETTING_AND_PURGE",
        "EPISTEMIC_ROLE_APPLICABILITY",
        "PROJECT_DOMAIN_ISOLATION",
        "HARMFUL_MEMORY_ABSTENTION",
    }


def test_e01b_uses_closed_status_stage_classification_and_reason_codes() -> None:
    fixture, _cases = load_fixture()
    report = run_fixture()
    allowed_statuses = set(fixture["status_values"])
    allowed_stages = set(fixture["stage_values"])
    allowed_classifications = set(fixture["classification_values"])
    allowed_codes = set(fixture["reason_codes"])

    assert report["schema"] == REPORT_SCHEMA
    assert all(receipt["status"] in allowed_statuses for receipt in report["receipts"])
    assert all(receipt["stage"] in allowed_stages for receipt in report["receipts"])
    assert all(
        receipt["classification"] in allowed_classifications
        for receipt in report["receipts"]
    )
    assert all(receipt["reason_code"] in allowed_codes for receipt in report["receipts"])


def test_e01b_frozen_base_production_result_and_unsupported_boundaries_are_exact() -> None:
    report = run_fixture()
    matrix = {
        receipt["capability"]: (
            receipt["status"],
            receipt["classification"],
            receipt["reason_code"],
        )
        for receipt in report["receipts"]
    }

    assert report["summary"] == {
        "case_count": 12,
        "pass_count": 6,
        "fail_count": 0,
        "not_exercised_count": 6,
        "classification_counts": {
            "conformance": 6,
            "fixture_mismatch": 2,
            "unsupported_semantic": 4,
        },
        "expectation_mismatch_count": 0,
    }
    assert matrix == {
        "authority_source_admission": (
            "pass",
            "conformance",
            "SOURCE_ADMISSION_CONFORMS",
        ),
        "correction_currentness": (
            "pass",
            "conformance",
            "CURRENTNESS_CORRECTION_CONFORMS",
        ),
        "kind_constrained_retrieval": (
            "pass",
            "conformance",
            "KIND_FILTER_CONFORMS",
        ),
        "generic_epistemic_role": (
            "not_exercised",
            "fixture_mismatch",
            "UNSUPPORTED_GENERIC_EPISTEMIC_ROLE",
        ),
        "explicit_scope_applicability": (
            "pass",
            "conformance",
            "SCOPE_FILTER_CONFORMS",
        ),
        "project_domain_hard_gate": (
            "not_exercised",
            "fixture_mismatch",
            "UNSUPPORTED_PROJECT_DOMAIN_HARD_GATE",
        ),
        "reversible_delete_restore": (
            "pass",
            "conformance",
            "REVERSIBLE_DELETE_RESTORE_CONFORMS",
        ),
        "purge_terminality_residue": (
            "pass",
            "conformance",
            "PURGE_TERMINAL_RESIDUE_FREE",
        ),
        "derived_dependency_invalidation": (
            "not_exercised",
            "unsupported_semantic",
            "UNSUPPORTED_DERIVED_DEPENDENCY_LINEAGE",
        ),
        "eviction_decay_retirement": (
            "not_exercised",
            "unsupported_semantic",
            "UNSUPPORTED_EVICTION_DECAY_RETIREMENT",
        ),
        "same_id_recreation_after_purge": (
            "not_exercised",
            "unsupported_semantic",
            "UNSUPPORTED_CALLER_STABLE_ID_RECREATION",
        ),
        "procedure_precondition_transfer": (
            "not_exercised",
            "unsupported_semantic",
            "UNSUPPORTED_PROCEDURE_PRECONDITIONS",
        ),
    }


def test_e01b_repeats_are_deterministic_and_use_only_disposable_local_core() -> None:
    report = run_fixture(repeats=3)

    assert report["determinism"]["repeats"] == 3
    assert report["determinism"]["repeat_deterministic"] is True
    assert report["execution_scope"] == {
        "condition": "production_core_at_frozen_coordinator_base",
        "frozen_base": COORDINATOR_BASE,
        "production_code_changed": False,
        "declared_capability_count": 12,
        "production_path_exercised_capability_count": 6,
        "not_exercised_capability_count": 6,
        "complete_capability_coverage": False,
        "temporary_isolated_store": True,
        "operator_core_connected": False,
        "external_systems_exercised": False,
        "network_calls": 0,
        "model_calls": 0,
        "python": "3.12",
    }
    assert report["privacy"]["temporary_stores_removed"] is True


def test_e01b_report_omits_raw_values_and_runtime_identifiers() -> None:
    report = run_fixture()
    rendered = json.dumps(report, sort_keys=True)
    reference = json.loads(REFERENCE_FIXTURES.read_text(encoding="utf-8"))

    assert "E01B_SYNTHETIC_" not in rendered
    assert re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        rendered,
        flags=re.IGNORECASE,
    ) is None
    for scenario in reference["scenarios"]:
        for event in scenario["events"]:
            if event.get("value"):
                assert event["value"] not in rendered


def test_frozen_e01b_report_matches_twenty_repeat_execution() -> None:
    frozen = json.loads(FROZEN_REPORT.read_text(encoding="utf-8"))

    assert frozen["fixture_sha256"] == hashlib.sha256(FIXTURES.read_bytes()).hexdigest()
    assert frozen == run_fixture(repeats=20)
