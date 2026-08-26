from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

from bench.zf013_project_graph_benchmark import (
    RELATION_FAMILIES,
    _corpus,
    _execute,
    _query_result,
    evaluate_profile_gates,
    load_contract,
    load_fixture,
    run,
    validate_relation_ablation_result,
)


def test_frozen_fixture_is_sanitized_and_covers_the_lane_matrix() -> None:
    fixture = load_fixture()
    records = fixture["records"]
    relations = fixture["relations"]
    queries = fixture["queries"]

    assert fixture["fixture_revision"] == "2026-08-25.production-ranker"
    assert len(relations) == 23
    assert {record["state"] for record in records} == {"current", "stale", "deleted", "purged"}
    assert {relation["family"] for relation in relations} == set(RELATION_FAMILIES)
    assert any(relation["source"] == relation["target"] for relation in relations)
    assert {query["target_relation_family"] for query in queries} == set(RELATION_FAMILIES)
    assert {query["target_relation_family"] for query in queries if query["ablation_case"]} == set(
        RELATION_FAMILIES
    )
    assert sum(query["ablation_case"] for query in queries) == len(RELATION_FAMILIES)
    assert all("C:\\" not in record["text"] for record in records)
    assert all("https://" not in record["text"] for record in records)


def test_benchmark_compares_controls_and_passes_graph_gates() -> None:
    report = run(warm_repetitions=2)
    profiles = report["profiles"]
    one_hop = profiles["lexical_typed_one_hop"]["metrics"]
    two_hop = profiles["bounded_typed_two_hop"]["metrics"]
    proxy = profiles["stdlib_lexical_proxy"]["metrics"]
    structured = profiles["structured_project_filter"]["metrics"]

    assert report["status"] == "harness_self_test_passed"
    assert report["production_retrieval_v3"] == {
        "status": "lexical_ranker_exercised",
        "scope": (
            "checkout-local LexicalV3 over fixture-supplied current and project-eligible IDs"
        ),
        "full_retrieval_facade": "not_exercised",
    }
    assert report["typed_graph_implementation"] == {
        "module": "allthecontext.project_graph",
        "status": "exercised_ephemeral_candidate",
        "runtime_wiring": False,
    }
    assert proxy["wrong_project_disclosures"] == 1
    assert structured["wrong_project_disclosures"] == 0
    assert one_hop["required_evidence_recall"] == 0.962963
    assert one_hop["project_caos"] == 0.888889
    assert two_hop["required_evidence_recall"] == 1.0
    assert two_hop["project_caos"] == 1.0
    assert two_hop["stale_disclosures"] == 0
    assert two_hop["deleted_disclosures"] == 0
    assert two_hop["purged_disclosures"] == 0
    assert two_hop["unnecessary_disclosures"] == 0
    assert two_hop["accepted_cycle_revisit_count"] == 0
    assert two_hop["accepted_self_edge_count"] == 0
    assert two_hop["fanout_truncation_count"] == 1
    assert two_hop["bound_violation_count"] == 0
    assert all(
        gates["status"] == "harness_self_test_passed" for gates in report["profile_gates"].values()
    )
    assert report["relation_normalization"]["eligible_relation_count"] == 16
    assert report["relation_normalization"]["rejected_illegal_edge_evidence"] == {
        "cross_project": 1,
        "cycle_edge": 1,
        "ineligible_target_deleted": 1,
        "ineligible_target_purged": 1,
        "ineligible_target_stale": 2,
        "self_edge": 1,
    }

    ablations = report["relation_ablations"]
    assert all(
        result["decision_scope"] == "synthetic_integration_hypothesis"
        and result["promotion_evidence"] is False
        for result in ablations.values()
    )
    assert ablations["supersedes"]["decision"] == "kill"
    assert {
        family for family in RELATION_FAMILIES if ablations[family]["decision"] == "keep"
    } == set(RELATION_FAMILIES) - {"supersedes"}


def test_receipts_are_stable_under_repetition_and_fixture_ordering() -> None:
    fixture = load_fixture()
    first = run(warm_repetitions=2)
    second = run(warm_repetitions=2)
    reordered = copy.deepcopy(fixture)
    for key in ("records", "relations", "queries"):
        reordered[key].reverse()
    reordered_report = run(reordered, warm_repetitions=1)

    assert first["receipts"] == second["receipts"] == reordered_report["receipts"]
    assert first["contract"]["fixture_sha256"] == reordered_report["contract"]["fixture_sha256"]
    assert first["profile_gates"] == reordered_report["profile_gates"]
    rendered = json.dumps(first, sort_keys=True)
    assert "atlas-" not in rendered
    assert "Beacon" not in rendered
    assert "release lane" not in rendered


def test_machine_readable_graph_gate_rejects_wrong_project_disclosure() -> None:
    report = run(warm_repetitions=1)
    broken = copy.deepcopy(report)
    broken["profiles"]["bounded_typed_two_hop"]["metrics"]["wrong_project_disclosures"] = 1

    gates = evaluate_profile_gates(broken, "bounded_typed_two_hop")

    assert gates["status"] == "harness_self_test_failed"
    assert gates["gates"]["wrong_project_disclosure"] is False


def _non_latency_metrics(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles = report["profiles"]
    assert isinstance(profiles, dict)
    return {
        str(name): {
            str(key): value for key, value in metrics["metrics"].items() if key != "warm_latency"
        }
        for name, metrics in profiles.items()
    }


def _profile_orderings(fixture: dict[str, Any]) -> dict[str, tuple[tuple[str, ...], ...]]:
    corpus = _corpus(fixture)
    contract = load_contract()
    profile_names = (
        "stdlib_lexical_proxy",
        "structured_project_filter",
        "deterministic_project_context_capsule",
        "lexical_typed_one_hop",
        "bounded_typed_two_hop",
    )
    return {
        profile_name: tuple(
            _query_result(corpus, query, _execute(corpus, query, profile_name, contract)).result_ids
            for query in corpus.queries
        )
        for profile_name in profile_names
    }


def test_ineligible_relations_are_paired_noninterference_cases() -> None:
    baseline_fixture = load_fixture()
    cases: list[tuple[str, dict[str, str], dict[str, object] | None]] = [
        (
            "cross_project",
            {"source": "atlas-release", "family": "depends_on", "target": "beacon-test"},
            None,
        ),
        (
            "ineligible_target_stale",
            {"source": "atlas-release", "family": "depends_on", "target": "atlas-decision-old"},
            None,
        ),
        (
            "ineligible_target_deleted",
            {"source": "atlas-release", "family": "depends_on", "target": "atlas-deleted"},
            None,
        ),
        (
            "ineligible_target_purged",
            {"source": "atlas-release", "family": "depends_on", "target": "atlas-purged"},
            None,
        ),
        (
            "unknown_endpoint",
            {"source": "atlas-release", "family": "depends_on", "target": "unknown-node"},
            None,
        ),
        (
            "ineligible_target_tentative",
            {"source": "atlas-release", "family": "depends_on", "target": "atlas-tentative"},
            {
                "id": "atlas-tentative",
                "project_id": "project-atlas",
                "state": "tentative",
                "text": "Synthetic tentative node",
            },
        ),
        (
            "self_edge",
            {"source": "atlas-release", "family": "depends_on", "target": "atlas-release"},
            None,
        ),
        (
            "cycle_edge",
            {
                "source": "atlas-chain-target",
                "family": "implements",
                "target": "atlas-chain-mid",
            },
            None,
        ),
    ]
    baseline = run(baseline_fixture, warm_repetitions=1)
    baseline_orderings = _profile_orderings(baseline_fixture)

    for reason, relation, extra_record in cases:
        mutated = copy.deepcopy(baseline_fixture)
        if extra_record is not None:
            mutated["records"].append(extra_record)
        mutated["relations"].append(relation)
        candidate = run(mutated, warm_repetitions=1)

        assert _non_latency_metrics(candidate) == _non_latency_metrics(baseline)
        assert candidate["profile_gates"] == baseline["profile_gates"]
        assert candidate["receipts"] == baseline["receipts"]
        assert _profile_orderings(mutated) == baseline_orderings
        evidence = candidate["relation_normalization"]["rejected_illegal_edge_evidence"]
        baseline_evidence = baseline["relation_normalization"]["rejected_illegal_edge_evidence"]
        assert evidence[reason] == baseline_evidence.get(reason, 0) + 1


def test_ablation_validation_fails_closed_for_malformed_or_unknown_decisions() -> None:
    report = run(warm_repetitions=1)
    valid = copy.deepcopy(report["relation_ablations"]["belongs_to"])
    contract = load_contract()

    assert validate_relation_ablation_result("belongs_to", valid, contract) is True

    malformed = copy.deepcopy(valid)
    malformed["decision"] = "maybe"
    assert validate_relation_ablation_result("belongs_to", malformed, contract) is False

    malformed = copy.deepcopy(valid)
    malformed["required_evidence_recall_delta"] = float("nan")
    assert validate_relation_ablation_result("belongs_to", malformed, contract) is False

    malformed = copy.deepcopy(valid)
    malformed["decision"] = "keep"
    malformed["required_evidence_recall_delta"] = 0.0
    malformed["project_caos_delta"] = 0.0
    assert validate_relation_ablation_result("belongs_to", malformed, contract) is False

    assert validate_relation_ablation_result("unknown_family", valid, contract) is False


def test_harness_imports_only_the_ephemeral_graph_candidate_from_product_code() -> None:
    source_path = Path("bench/zf013_project_graph_benchmark.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports.extend(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert {name for name in imports if name.startswith("allthecontext")} == {
        "allthecontext.lexical_v3",
        "allthecontext.project_graph",
    }
    assert not any(
        name.startswith(
            (
                "allthecontext.core",
                "allthecontext.storage",
                "allthecontext.mcp",
                "allthecontext.capture",
            )
        )
        for name in imports
    )
    assert "sqlite3" in imports
    assert not any(name.startswith(("httpx", "requests")) for name in imports)
