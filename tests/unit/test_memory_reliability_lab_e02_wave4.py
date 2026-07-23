from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from bench.memory_reliability_lab_e02_wave4 import (
    CLASSIFICATIONS,
    FIXTURE,
    GOVERNANCE_BASE,
    REPORT_SCHEMA,
    load_fixture,
    run_fixture,
)

FROZEN_REPORT = (
    Path(__file__).parents[2]
    / "bench"
    / "reports"
    / "memory_reliability_e02_wave4.json"
)


def test_e02_wave4_fixture_freezes_six_gaps_before_execution() -> None:
    fixture, cases = load_fixture()

    assert fixture["governance_base"] == GOVERNANCE_BASE
    assert fixture["expectations_frozen_before_execution"] is True
    assert fixture["expectation_authorship"] == {
        "method": "hand-authored against the known frozen production codebase",
        "blind_independent_suite": False,
        "frozen_before_e02_execution": True,
    }
    assert fixture["content_policy"] == {
        "synthetic": True,
        "symbolic_values_only": True,
        "real_personal_context": False,
        "external_code": False,
        "network_access": False,
        "operator_core_access": False,
        "provider_or_model_access": False,
    }
    assert len(cases) == 6
    assert {case.case_id for case in cases} == {
        "generic_epistemic_role",
        "project_and_domain_applicability",
        "dependency_lineage_and_invalidation",
        "eviction_decay_and_procedure_retirement",
        "same_identifier_after_terminal_purge",
        "procedure_preconditions_and_transfer",
    }


def test_e02_wave4_gap_and_boundary_classifications_are_exact() -> None:
    report = run_fixture()
    cases = {receipt["case_id"]: receipt for receipt in report["receipts"]}

    assert report["schema"] == REPORT_SCHEMA
    assert report["summary"] == {
        "gap_count": 6,
        "classification_counts": {
            "CONTRADICTED_OBSERVED": 0,
            "NOT_EXERCISED": 1,
            "SUPPORTED_OBSERVED": 0,
            "UNSUPPORTED": 5,
        },
        "case_expectation_mismatch_count": 0,
        "probe_expectation_mismatch_count": 0,
        "evaluation_error_count": 0,
    }
    assert {
        case_id: receipt["classification"] for case_id, receipt in cases.items()
    } == {
        "generic_epistemic_role": "UNSUPPORTED",
        "project_and_domain_applicability": "UNSUPPORTED",
        "dependency_lineage_and_invalidation": "UNSUPPORTED",
        "eviction_decay_and_procedure_retirement": "UNSUPPORTED",
        "same_identifier_after_terminal_purge": "NOT_EXERCISED",
        "procedure_preconditions_and_transfer": "UNSUPPORTED",
    }
    assert all(
        set(receipt["probe_classifications"].values()) <= CLASSIFICATIONS
        for receipt in cases.values()
    )
    assert all(
        receipt["probe_classifications"]
        == receipt["expected_probe_classifications"]
        for receipt in cases.values()
    )


def test_e02_wave4_adversarial_substitutions_remain_visible() -> None:
    cases = {
        receipt["case_id"]: receipt["observed"]
        for receipt in run_fixture(repeats=1)["receipts"]
    }

    role = cases["generic_epistemic_role"]
    assert role["semantic_role_peer_count"] == 2
    assert role["kind_substitution_result_count"] == 1
    applicability = cases["project_and_domain_applicability"]
    assert applicability["expected_and_match_count"] == 1
    assert applicability["project_label_without_domain_result_count"] == 2
    assert applicability["current_project_only_result_count"] == 1
    lineage = cases["dependency_lineage_and_invalidation"]
    assert lineage["derived_version_before_source_correction"] == 1
    assert lineage["derived_version_after_source_correction"] == 1
    assert lineage["derived_retrievable_after_source_correction"] is True
    procedure = cases["procedure_preconditions_and_transfer"]
    assert procedure["wrong_current_project_result_count"] == 1
    assert procedure["wrong_explicit_scope_result_count"] == 0


def test_e02_wave4_adjacent_lifecycle_boundaries_are_not_overclaimed() -> None:
    cases = {
        receipt["case_id"]: receipt["observed"]
        for receipt in run_fixture(repeats=1)["receipts"]
    }

    lifecycle = cases["eviction_decay_and_procedure_retirement"]
    assert lifecycle["expired_record_remains_canonical"] is True
    assert lifecycle["expired_record_retrievable"] is False
    assert lifecycle["confidence_before_search"] == 0.9
    assert lifecycle["confidence_after_search"] == 0.9
    purge = cases["same_identifier_after_terminal_purge"]
    assert purge["caller_selected_record_id_rejected"] is True
    assert purge["exact_same_identifier_attempted"] is False
    assert purge["fresh_recreation_uses_distinct_identifier"] is True
    assert purge["old_identifier_tombstone_count"] == 1


def test_e02_wave4_is_deterministic_private_and_disposable() -> None:
    report = run_fixture(repeats=3)
    rendered = json.dumps(report, sort_keys=True)

    assert report["determinism"]["repeat_deterministic"] is True
    assert report["execution_scope"] == {
        "condition": "frozen_production_core_disposable_synthetic_stores",
        "production_code_changed": False,
        "declared_gap_count": 6,
        "executed_boundary_probe_count": 15,
        "temporary_isolated_store": True,
        "operator_core_connected": False,
        "external_code_or_systems_exercised": False,
        "network_calls": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "raw_personal_context": False,
    }
    assert "E02_SYNTHETIC_" not in rendered
    assert re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        rendered,
        flags=re.IGNORECASE,
    ) is None


def test_e02_wave4_verifies_privacy_safe_import_origin() -> None:
    origin = run_fixture(repeats=1)["execution_origin_receipt"]

    assert origin == {
        "governance_commit_sha": GOVERNANCE_BASE,
        "import_origin_verified_to_worker_worktree": True,
        "relative_imported_module_paths": {
            "allthecontext": "packages/allthecontext/src/allthecontext/__init__.py",
            "allthecontext.core.service": (
                "packages/allthecontext/src/allthecontext/core/service.py"
            ),
            "allthecontext.models": "packages/allthecontext/src/allthecontext/models.py",
            "allthecontext.storage": "packages/allthecontext/src/allthecontext/storage.py",
        },
        "absolute_paths_recorded": False,
        "pre_verification_receipts_invalidated": True,
    }


def test_e02_wave4_boundary_receipt_keeps_core_authoritative() -> None:
    receipt = run_fixture(repeats=1)["implementation_boundary_receipt"]

    assert receipt["first_capability"]["capability"] == "generic_epistemic_role"
    assert "never infer role from kind" in receipt["first_capability"][
        "smallest_safe_boundary"
    ]
    assert any(
        "Core remains the sole canonical authority" in boundary
        for boundary in receipt["authority_boundaries"]
    )
    assert any(
        "sidecar graph would create a second authority" in risk
        for risk in receipt["migration_risks"]
    )


def test_frozen_e02_wave4_report_matches_ten_repeat_execution() -> None:
    frozen = json.loads(FROZEN_REPORT.read_text(encoding="utf-8"))

    assert frozen["fixture_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert frozen == run_fixture(repeats=10)
