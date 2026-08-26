from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from bench.zf013_project_graph_benchmark import (
    RELATION_FAMILIES,
    evaluate_profile_gates,
    load_fixture,
    run,
)


def test_frozen_fixture_is_sanitized_and_covers_the_lane_matrix() -> None:
    fixture = load_fixture()
    records = fixture["records"]
    relations = fixture["relations"]
    queries = fixture["queries"]

    assert fixture["fixture_revision"] == "2026-08-25.lane-a"
    assert {record["state"] for record in records} == {"current", "stale", "deleted", "purged"}
    assert {relation["family"] for relation in relations} == set(RELATION_FAMILIES)
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
    v3 = profiles["retrieval_v3_current"]["metrics"]
    structured = profiles["structured_project_filter"]["metrics"]

    assert report["overall_status"] == "passed"
    assert v3["wrong_project_disclosures"] == 1
    assert structured["wrong_project_disclosures"] == 0
    assert one_hop["required_evidence_recall"] == 0.962963
    assert one_hop["project_caos"] == 0.888889
    assert two_hop["required_evidence_recall"] == 1.0
    assert two_hop["project_caos"] == 1.0
    assert two_hop["stale_disclosures"] == 0
    assert two_hop["deleted_disclosures"] == 0
    assert two_hop["purged_disclosures"] == 0
    assert two_hop["unnecessary_disclosures"] == 0
    assert two_hop["cycle_revisit_count"] == 1
    assert two_hop["fanout_truncation_count"] == 1
    assert two_hop["bound_violation_count"] == 0
    assert all(gates["status"] == "passed" for gates in report["profile_gates"].values())

    ablations = report["relation_ablations"]
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

    assert gates["status"] == "failed"
    assert gates["gates"]["wrong_project_disclosure"] is False


def test_harness_has_no_production_or_external_runtime_imports() -> None:
    source_path = Path("bench/zf013_project_graph_benchmark.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports.extend(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any(name.startswith("allthecontext") for name in imports)
    assert not any(name.startswith(("httpx", "requests", "sqlite3")) for name in imports)
