from __future__ import annotations

import itertools
from dataclasses import fields, replace

import pytest
from allthecontext.project_graph import (
    GraphAbstentionReason,
    GraphDirection,
    ProjectGraphError,
    ProjectGraphEvidence,
    ProjectRelationFamily,
    build_project_graph,
)

from bench.zf013_graph_adversarial import load_matrix

PROJECT = "project-alpha"
AS_OF = "2026-08-25T00:00:00Z"


def _edge(
    evidence_id: str,
    subject_id: str,
    relation: ProjectRelationFamily,
    object_id: str,
    *,
    authorized: bool = True,
    lifecycle_eligible: bool = True,
    ambiguous: bool = False,
    purged: bool = False,
    object_project_id: str = PROJECT,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> ProjectGraphEvidence:
    return ProjectGraphEvidence(
        evidence_id=evidence_id,
        project_id=PROJECT,
        subject_id=subject_id,
        subject_project_id=PROJECT,
        subject_kind="artifact",
        relation=relation,
        object_id=object_id,
        object_project_id=object_project_id,
        object_kind="artifact",
        provenance_ids=(f"provenance-{evidence_id}",),
        dependency_ids=(f"dependency-{evidence_id}",),
        authorized=authorized,
        lifecycle_eligible=lifecycle_eligible,
        ambiguous=ambiguous,
        purged=purged,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def test_actual_graph_maps_every_frozen_oracle_case_to_conformance_evidence() -> None:
    case_ids = {case["case_id"] for case in load_matrix()["cases"]}
    covered = {
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

    assert covered == case_ids


def test_actual_graph_authorization_project_and_assignment_attacks_are_noninterfering() -> None:
    valid = _edge("valid", "alpha-root", ProjectRelationFamily.DEPENDS_ON, "alpha-child")
    baseline = build_project_graph(PROJECT, (valid,), as_of=AS_OF)
    attacks = (
        replace(valid, evidence_id="unauthorized", authorized=False),
        replace(valid, evidence_id="ambiguous", ambiguous=True),
        replace(valid, evidence_id="stale", lifecycle_eligible=False),
        replace(valid, evidence_id="purged", purged=True),
        replace(valid, evidence_id="foreign", object_project_id="project-beta"),
        replace(valid, evidence_id="future", valid_from="2026-08-26T00:00:00Z"),
        replace(valid, evidence_id="expired", valid_to="2026-08-24T00:00:00Z"),
    )
    attacked = build_project_graph(PROJECT, (valid, *attacks), as_of=AS_OF)

    assert attacked.stable_json() == baseline.stable_json()
    assert attacked.revision == baseline.revision
    assert attacked.nodes == baseline.nodes
    assert attacked.edges == baseline.edges
    assert attacked.abstentions == baseline.abstentions


def test_actual_graph_temporal_delete_purge_and_stale_closure() -> None:
    old = _edge(
        "old",
        "alpha-root",
        ProjectRelationFamily.DEPENDS_ON,
        "alpha-old",
        valid_to="2026-08-10T00:00:00Z",
    )
    current = _edge(
        "current",
        "alpha-root",
        ProjectRelationFamily.DEPENDS_ON,
        "alpha-current",
        valid_from="2026-08-10T00:00:00Z",
    )
    purged = _edge(
        "purged",
        "alpha-root",
        ProjectRelationFamily.DEPENDS_ON,
        "alpha-purged",
        purged=True,
    )
    stale_descendant = _edge(
        "stale-descendant",
        "alpha-old",
        ProjectRelationFamily.IMPLEMENTS,
        "alpha-stale-derived",
        lifecycle_eligible=False,
    )

    historical = build_project_graph(
        PROJECT,
        (old, current, purged, stale_descendant),
        as_of="2026-08-09T00:00:00Z",
    )
    latest = build_project_graph(PROJECT, (old, current, purged, stale_descendant), as_of=AS_OF)

    assert {edge.object_id for edge in historical.edges} == {"alpha-old"}
    assert {edge.object_id for edge in latest.edges} == {"alpha-current"}
    assert "alpha-purged" not in historical.stable_json()
    assert "alpha-purged" not in latest.stable_json()
    assert "alpha-stale-derived" not in latest.stable_json()
    assert historical.revision != latest.revision


def test_actual_graph_rejects_illegal_topology_and_replay_inflation() -> None:
    forward = _edge(
        "e-alpha-forward",
        "alpha-root",
        ProjectRelationFamily.DEPENDS_ON,
        "alpha-child",
    )
    replayed = build_project_graph(PROJECT, (forward, forward), as_of=AS_OF)
    baseline = build_project_graph(PROJECT, (forward,), as_of=AS_OF)
    assert replayed.stable_json() == baseline.stable_json()

    attacked = build_project_graph(
        PROJECT,
        (
            forward,
            _edge(
                "e-cycle",
                "alpha-child",
                ProjectRelationFamily.DEPENDS_ON,
                "alpha-root",
            ),
            _edge("self", "alpha-self", ProjectRelationFamily.BLOCKS, "alpha-self"),
        ),
        as_of=AS_OF,
    )
    reasons = {item.reason for item in attacked.abstentions}
    assert GraphAbstentionReason.CYCLE_DETECTED in reasons
    assert GraphAbstentionReason.SELF_EDGE in reasons
    assert attacked.edges == baseline.edges
    assert attacked.revision == baseline.revision

    conflicting = build_project_graph(
        PROJECT,
        (
            forward,
            replace(
                forward,
                evidence_id="conflicting",
                relation=ProjectRelationFamily.BLOCKS,
            ),
        ),
        as_of=AS_OF,
    )
    assert not conflicting.edges
    assert GraphAbstentionReason.DUPLICATE_CONFLICT in {
        item.reason for item in conflicting.abstentions
    }


def test_actual_graph_two_hop_expansion_is_bounded_under_high_fanout() -> None:
    fanout = tuple(
        _edge(
            f"fan-{index:02d}",
            "fan-root",
            ProjectRelationFamily.DEPENDS_ON,
            f"fan-child-{index:02d}",
        )
        for index in range(20)
    )
    second = _edge(
        "second-hop",
        "fan-child-00",
        ProjectRelationFamily.IMPLEMENTS,
        "fan-second-hop",
    )
    third = _edge(
        "third-hop",
        "fan-second-hop",
        ProjectRelationFamily.TESTED_BY,
        "fan-third-hop",
    )
    graph = build_project_graph(PROJECT, (*fanout, second, third), fanout_cap=16)
    expansion = graph.expand_two_hop(
        "fan-root",
        direction=GraphDirection.OUTGOING,
        node_cap=12,
        edge_cap=24,
    )

    assert len(expansion.node_ids) <= 12
    assert "fan-third-hop" not in expansion.node_ids
    assert expansion.omitted_nodes > 0
    assert len(expansion.edges) <= 24


def test_actual_graph_fresh_rebuild_is_deterministic_under_reordering() -> None:
    evidence = (
        _edge("one", "alpha-root", ProjectRelationFamily.DEPENDS_ON, "alpha-a"),
        _edge("two", "alpha-a", ProjectRelationFamily.IMPLEMENTS, "alpha-b"),
        _edge("three", "alpha-b", ProjectRelationFamily.TESTED_BY, "alpha-c"),
    )
    expected = build_project_graph(PROJECT, evidence, as_of=AS_OF).stable_json()

    for ordering in itertools.permutations(evidence):
        assert build_project_graph(PROJECT, ordering, as_of=AS_OF).stable_json() == expected


def test_actual_graph_has_no_content_surface_and_refuses_secret_or_path_tokens() -> None:
    field_names = {field.name for field in fields(ProjectGraphEvidence)}
    assert field_names.isdisjoint(
        {"content", "text", "prompt", "instruction", "raw_context", "tool_args"}
    )

    for unsafe in (
        "raw imported instruction",
        "../private",
        r"C:\\private",
        "sk-abcdefghijklmnopqrstuvwxyz123456",
    ):
        with pytest.raises(ProjectGraphError):
            _edge(unsafe, "alpha-root", ProjectRelationFamily.DEPENDS_ON, "alpha-child")

    graph = build_project_graph(
        PROJECT,
        (_edge("safe", "alpha-root", ProjectRelationFamily.DEPENDS_ON, "alpha-child"),),
    )
    result = graph.expand_two_hop("sk-abcdefghijklmnopqrstuvwxyz123456")
    assert result.origin_id == "invalid-origin"
    assert "sk-" not in str(result.to_dict())
