from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path

from bench.zf013_graph_adversarial import (
    Edge,
    GraphInput,
    Node,
    Query,
    append_edges,
    append_nodes,
    base_graph,
    load_matrix,
    normalize_edges,
    project,
    projection_differences,
    validate_projection,
)

AS_OF = "2026-08-25T00:00:00Z"
OLD_AS_OF = "2026-08-09T23:59:59Z"
ALPHA_QUERY = Query("project-alpha", ("alpha-root",), as_of=AS_OF)


def test_matrix_is_frozen_and_covers_the_complete_zf013_attack_set() -> None:
    matrix = load_matrix()

    assert matrix["status"] == "frozen_preimplementation"
    assert matrix["synthetic_only"] is True
    assert matrix["governance_base_commit"] == "fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2"
    assert matrix["production_runtime_touched"] is False
    assert matrix["bounds"] == {
        "max_hops": 2,
        "max_nodes": 12,
        "edge_direction": "source_to_derived",
        "input_order": "semantically irrelevant",
    }
    assert matrix["observable_dimensions"] == [
        "content",
        "reason_codes",
        "revision",
        "counts",
        "ordering",
        "receipts",
    ]
    case_ids = {case["case_id"] for case in matrix["cases"]}
    assert case_ids == {
        "ZF013-C01-AUTHORIZATION-FIRST-NONINTERFERENCE",
        "ZF013-C02-CROSS-PROJECT-ISOLATION",
        "ZF013-C03-AMBIGUOUS-ASSIGNMENT-ABSTENTION",
        "ZF013-C04-CORRECTION-SUPERSESSION",
        "ZF013-C05-AS-OF-HISTORICAL",
        "ZF013-C06-DELETE-PURGE-CLOSURE",
        "ZF013-C07-STALE-DEPENDENCY-INVALIDATION",
        "ZF013-C08-CYCLE-REJECTION",
        "ZF013-C09-SELF-EDGE-REJECTION",
        "ZF013-C10-DUPLICATE-EDGE-IDEMPOTENCE",
        "ZF013-C11-HIGH-FANOUT-BOUNDED-TWO-HOP",
        "ZF013-C12-DETERMINISTIC-REBUILD-REORDER",
        "ZF013-C13-SECRET-IMPORTED-INSTRUCTION-INERTNESS",
        "ZF013-C14-UNAUTHORIZED-DIMENSION-NONINTERFERENCE",
    }
    invariant_ids = {item["invariant_id"] for item in matrix["invariants"]}
    assert all(set(case["covers"]) <= invariant_ids for case in matrix["cases"])
    assert all(
        case["failure"] and case["kill"].startswith("KILL_ZF013")
        for case in matrix["cases"]
    )
    assert matrix["kill_criteria"]["status_if_all_pass"] == "RETAIN_ZF013_SAFETY_ORACLE"
    assert matrix["kill_criteria"]["kill_rule"].startswith("Any kill_on_any item")
    assert matrix["kill_criteria"]["hold_rule"].startswith("Any hold_on item")


def test_oracle_is_independent_of_production_runtime() -> None:
    oracle_path = Path(__file__).parents[2] / "bench" / "zf013_graph_adversarial.py"
    source = oracle_path.read_text(encoding="utf-8")

    assert "allthecontext" not in source
    assert "sqlite" not in source.lower()
    assert "http" not in source.lower()


def test_authorization_first_noninterference_covers_every_observable_dimension() -> None:
    baseline = project(base_graph(), ALPHA_QUERY)
    attack_nodes = (
        Node(
            "unauthorized-alpha-canary",
            "unauthorized-alpha-canary",
            "project-alpha",
            "unauthorized-content",
            authorized=False,
        ),
        Node(
            "unauthorized-beta-canary",
            "unauthorized-beta-canary",
            "project-beta",
            "unauthorized-beta-content",
            authorized=False,
        ),
        Node(
            "unauthorized-ambiguous",
            "unauthorized-ambiguous",
            "project-alpha",
            "unauthorized-ambiguous-content",
            authorized=False,
            assignment="ambiguous",
        ),
        Node(
            "unauthorized-secret",
            "unauthorized-secret",
            "project-alpha",
            "synthetic-unauthorized-secret",
            authorized=False,
            secret_like=True,
        ),
    )
    attacked = append_edges(
        append_nodes(base_graph(), *attack_nodes),
        Edge("unauthorized-cycle", "unauthorized-alpha-canary", "unauthorized-alpha-canary"),
        Edge("unauthorized-cross-project", "unauthorized-alpha-canary", "unauthorized-beta-canary"),
    )

    assert projection_differences(baseline, project(attacked, ALPHA_QUERY)) == ()


def test_cross_project_edge_is_rejected_and_project_projection_is_unchanged() -> None:
    graph = base_graph()
    alpha_nodes = {
        node.node_id: node for node in graph.nodes if node.project_id == "project-alpha"
    }
    cross_project = Edge("e-cross-project", "alpha-root", "beta-root")
    beta_root = next(node for node in graph.nodes if node.node_id == "beta-root")
    normalized = normalize_edges(
        alpha_nodes | {"beta-root": beta_root},
        (*graph.edges, cross_project),
    )

    assert ("e-cross-project", "cross_project") in normalized.rejected
    baseline = project(graph, ALPHA_QUERY)
    with_beta_edge = project(append_edges(graph, cross_project), ALPHA_QUERY)
    assert projection_differences(baseline, with_beta_edge) == ()
    assert all("beta" not in value for value in with_beta_edge.content)
    assert all("beta" not in value for value in with_beta_edge.ordering)


def test_ambiguous_assignment_abstains_without_graph_or_receipt_output() -> None:
    result = project(base_graph(), Query("project-alpha", ("alpha-ambiguous",), as_of=AS_OF))

    assert result.content == ()
    assert result.ordering == ()
    assert result.receipts == ()
    assert dict(result.counts)["expanded_nodes"] == 0
    assert result.reason_codes == ("graph:abstain:ambiguous_assignment",)


def test_correction_supersession_and_stale_dependency_invalidation() -> None:
    result = project(base_graph(), ALPHA_QUERY)
    assert "alpha-corrected-v2" in result.content
    assert "alpha-fresh-derived-v2" in result.content
    assert "alpha-corrected-v1" not in result.content
    assert "alpha-stale-derived-v1" not in result.content
    assert all("alpha-corrected-v1" not in receipt.dependency_path for receipt in result.receipts)


def test_as_of_rebuild_keeps_old_revision_before_correction() -> None:
    historical = project(
        base_graph(), Query("project-alpha", ("alpha-root",), as_of=OLD_AS_OF)
    )
    current = project(base_graph(), ALPHA_QUERY)

    assert "alpha-corrected-v1" in historical.content
    assert "alpha-corrected-v2" not in historical.content
    assert "alpha-stale-derived-v1" in historical.content
    assert "alpha-corrected-v2" in current.content
    assert "alpha-corrected-v1" not in current.content
    assert historical.revision != current.revision


def test_delete_is_historical_but_terminal_purge_closes_current_and_past() -> None:
    before_delete = project(
        base_graph(), Query("project-alpha", ("alpha-root",), as_of="2026-08-14T23:59:59Z")
    )
    after_delete = project(
        base_graph(), Query("project-alpha", ("alpha-root",), as_of="2026-08-16T00:00:00Z")
    )
    before_purge = project(
        base_graph(), Query("project-alpha", ("alpha-root",), as_of="2026-08-17T00:00:00Z")
    )

    assert "alpha-deleted" in before_delete.content
    assert "alpha-deleted" not in after_delete.content
    assert "alpha-purged" not in before_purge.content
    assert "alpha-purged-derived" not in before_purge.content
    assert all(receipt.subject_id != "alpha-purged" for receipt in before_purge.receipts)
    assert all(
        receipt.subject_id != "alpha-purged-derived" for receipt in before_purge.receipts
    )


def test_cycles_and_self_edges_are_rejected_without_projection_effect() -> None:
    graph = base_graph()
    cycle_and_self = (
        Edge("e-cycle", "alpha-fresh-derived", "alpha-root"),
        Edge("e-self", "alpha-root", "alpha-root"),
    )
    eligible = {
        node.node_id: node
        for node in graph.nodes
        if (
            node.project_id == "project-alpha"
            and node.assignment == "assigned"
            and not node.purged_at
        )
    }
    normalized = normalize_edges(eligible, (*graph.edges, *cycle_and_self))

    assert ("e-cycle", "cycle") in normalized.rejected
    assert ("e-self", "self_edge") in normalized.rejected
    assert (
        projection_differences(
            project(graph, ALPHA_QUERY),
            project(append_edges(graph, *cycle_and_self), ALPHA_QUERY),
        )
        == ()
    )


def test_duplicate_edges_are_idempotent_in_all_projection_dimensions() -> None:
    graph = base_graph()
    duplicate = Edge("e-alpha-corrected-v2-duplicate", "alpha-root", "alpha-corrected-v2")
    conflicting = Edge(
        "e-alpha-corrected-v2-conflicting",
        "alpha-root",
        "alpha-corrected-v2",
        relation="conflicts",
    )
    normalized = normalize_edges(
        {
            node.node_id: node for node in graph.nodes if node.project_id == "project-alpha"
        },
        (*graph.edges, duplicate, conflicting),
    )

    assert normalized.duplicate_count == 1
    assert ("e-alpha-corrected-v2-conflicting", "conflicting_duplicate") in normalized.rejected
    assert (
        projection_differences(
            project(graph, ALPHA_QUERY), project(append_edges(graph, duplicate), ALPHA_QUERY)
        )
        == ()
    )


def test_high_fanout_is_bounded_to_two_hops_and_twelve_nodes() -> None:
    nodes = [Node("fan-root", "fan-root", "project-alpha", "fan-root")]
    edges: list[Edge] = []
    for index in range(20):
        child_id = f"fan-child-{index:02d}"
        nodes.append(Node(child_id, child_id, "project-alpha", child_id))
        edges.append(Edge(f"e-fan-{index:02d}", "fan-root", child_id))
    nodes.extend(
        [
            Node("fan-third-hop", "fan-third-hop", "project-alpha", "fan-third-hop"),
            Node("fan-second-hop", "fan-second-hop", "project-alpha", "fan-second-hop"),
        ]
    )
    edges.extend(
        [
            Edge("e-fan-second", "fan-child-00", "fan-second-hop"),
            Edge("e-fan-third", "fan-second-hop", "fan-third-hop"),
        ]
    )
    result = project(GraphInput(tuple(nodes), tuple(edges)), Query("project-alpha", ("fan-root",)))

    assert len(result.ordering) == 12
    assert "fan-third-hop" not in result.ordering
    assert dict(result.counts)["truncated"] == 1
    assert result.reason_codes == ("graph:bounded_two_hop",)
    assert all(receipt.depth <= 2 for receipt in result.receipts)


def test_rebuild_and_input_reordering_are_exactly_deterministic() -> None:
    graph = base_graph()
    expected = project(graph, ALPHA_QUERY)
    node_permutations = itertools.islice(itertools.permutations(graph.nodes), 24)
    edge_permutations = itertools.islice(itertools.permutations(graph.edges), 24)

    for nodes, edges in zip(node_permutations, edge_permutations, strict=False):
        rebuilt = project(GraphInput(nodes, edges), ALPHA_QUERY)
        assert projection_differences(expected, rebuilt) == ()


def test_secret_like_and_imported_instruction_nodes_are_inert() -> None:
    graph = base_graph()
    baseline = project(graph, ALPHA_QUERY)
    connected_markers = append_edges(
        graph,
        Edge("e-secret-marker", "alpha-root", "alpha-secret-like"),
        Edge("e-instruction-marker", "alpha-root", "alpha-imported-instruction"),
    )
    result = project(connected_markers, ALPHA_QUERY)

    assert projection_differences(baseline, result) == ()
    assert "synthetic-secret-like-marker" not in result.content
    assert "synthetic-imported-instruction-marker" not in result.content
    assert all("secret" not in receipt.subject_id for receipt in result.receipts)
    assert all("instruction" not in receipt.subject_id for receipt in result.receipts)


def test_unauthorized_dimension_noninterference_has_exact_six_dimension_contract() -> None:
    matrix = load_matrix()
    case = next(
        item
        for item in matrix["cases"]
        if item["case_id"] == "ZF013-C14-UNAUTHORIZED-DIMENSION-NONINTERFERENCE"
    )
    assert case["expected"] == (
        "content, reason_codes, revision, counts, ordering, and receipts are each exactly equal."
    )
    assert matrix["observable_dimensions"] == [
        "content",
        "reason_codes",
        "revision",
        "counts",
        "ordering",
        "receipts",
    ]


def test_reusable_oracle_reports_exact_dimension_failures() -> None:
    expected = project(base_graph(), ALPHA_QUERY)
    actual = replace(expected, content=(*expected.content, "unexpected-symbol"))

    assert validate_projection(actual, expected) == ("content",)
