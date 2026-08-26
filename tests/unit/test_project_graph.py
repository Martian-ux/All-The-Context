from dataclasses import replace

import pytest
from allthecontext.project_graph import (
    GraphAbstentionReason,
    GraphDirection,
    ProjectGraphError,
    ProjectGraphEvidence,
    ProjectRelationFamily,
    RelationBasis,
    build_project_graph,
)

PROJECT = "project-atlas"
AS_OF = "2026-08-25T00:00:00Z"


def _edge(
    evidence_id: str,
    subject: str,
    relation: ProjectRelationFamily,
    object_id: str,
    *,
    subject_kind: str = "artifact",
    object_kind: str = "artifact",
    provenance: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    authorized: bool = True,
    lifecycle_eligible: bool = True,
    ambiguous: bool = False,
    purged: bool = False,
    basis: RelationBasis = RelationBasis.EXPLICIT,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> ProjectGraphEvidence:
    return ProjectGraphEvidence(
        evidence_id=evidence_id,
        project_id=PROJECT,
        subject_id=subject,
        subject_project_id=PROJECT,
        subject_kind=subject_kind,
        relation=relation,
        object_id=object_id,
        object_project_id=PROJECT,
        object_kind=object_kind,
        provenance_ids=provenance,
        dependency_ids=dependencies,
        authorized=authorized,
        lifecycle_eligible=lifecycle_eligible,
        ambiguous=ambiguous,
        purged=purged,
        basis=basis,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def test_all_initial_families_preserve_lineage_and_receipts() -> None:
    evidence = (
        _edge(
            "belongs",
            "a",
            ProjectRelationFamily.BELONGS_TO,
            PROJECT,
            object_kind="project",
            basis=RelationBasis.STRUCTURAL,
            provenance=("prov-a",),
            dependencies=("dep-a",),
        ),
        _edge("supersedes", "b", ProjectRelationFamily.SUPERSEDES, "a"),
        _edge("depends", "c", ProjectRelationFamily.DEPENDS_ON, "b"),
        _edge("blocks", "d", ProjectRelationFamily.BLOCKS, "c"),
        _edge("implements", "e", ProjectRelationFamily.IMPLEMENTS, "d"),
        _edge("tested", "f", ProjectRelationFamily.TESTED_BY, "e"),
    )
    graph = build_project_graph(PROJECT, evidence, as_of=AS_OF)

    assert [edge.relation.value for edge in graph.edges] == [
        "belongs_to",
        "supersedes",
        "depends_on",
        "blocks",
        "implements",
        "tested_by",
    ]
    assert graph.edges[0].provenance_ids == ("prov-a",)
    assert graph.edges[0].dependency_ids == ("dep-a",)
    assert graph.edges[0].evidence_ids == ("belongs",)
    assert graph.nodes[-1].node_id == PROJECT
    assert graph.to_dict()["derived_read_only"] is True
    assert graph.stable_json() == graph.stable_json()


def test_revision_is_stable_under_input_reordering_and_project_isolated() -> None:
    first = _edge("one", "a", ProjectRelationFamily.DEPENDS_ON, "b")
    second = _edge("two", "b", ProjectRelationFamily.IMPLEMENTS, "c")
    left = build_project_graph(PROJECT, (first, second), as_of=AS_OF)
    right = build_project_graph(PROJECT, (second, first), as_of=AS_OF)
    assert left.revision == right.revision
    assert left.stable_json() == right.stable_json()

    other = replace(second, evidence_id="foreign", object_project_id="project-zephyr")
    isolated = build_project_graph(PROJECT, (first, other), as_of=AS_OF)
    baseline = build_project_graph(PROJECT, (first,), as_of=AS_OF)
    assert isolated.stable_json() == baseline.stable_json()
    assert isolated.revision == baseline.revision


def test_unsafe_evidence_is_exactly_noninterfering() -> None:
    valid = _edge("valid", "a", ProjectRelationFamily.DEPENDS_ON, "b")
    unsafe = (
        replace(valid, evidence_id="unauth", authorized=False),
        replace(valid, evidence_id="stale", lifecycle_eligible=False),
        replace(valid, evidence_id="ambiguous", ambiguous=True),
        replace(valid, evidence_id="purged", purged=True),
        replace(valid, evidence_id="foreign", object_project_id="project-zephyr"),
        replace(valid, evidence_id="future", valid_from="2026-08-26T00:00:00Z"),
        replace(valid, evidence_id="expired", valid_to="2026-08-24T00:00:00Z"),
    )
    baseline = build_project_graph(PROJECT, (valid,), as_of=AS_OF)
    attacked = build_project_graph(PROJECT, (valid, *unsafe), as_of=AS_OF)
    assert attacked.stable_json() == baseline.stable_json()
    assert attacked.revision == baseline.revision
    assert attacked.nodes == baseline.nodes
    assert attacked.edges == baseline.edges
    assert attacked.abstentions == baseline.abstentions


def test_inference_duplicates_cycles_and_self_edges_abstain() -> None:
    valid = _edge("valid", "a", ProjectRelationFamily.DEPENDS_ON, "b")
    invalid = (
        replace(valid, evidence_id="inferred", basis=RelationBasis.INFERRED),
        _edge("cycle", "b", ProjectRelationFamily.DEPENDS_ON, "a"),
        _edge("self", "c", ProjectRelationFamily.BLOCKS, "c"),
    )
    graph = build_project_graph(PROJECT, (valid, *invalid), as_of=AS_OF)
    reasons = {item.reason for item in graph.abstentions}
    assert {
        GraphAbstentionReason.INFERRED_RELATION_UNSUPPORTED,
        GraphAbstentionReason.CYCLE_DETECTED,
        GraphAbstentionReason.SELF_EDGE,
    } <= reasons
    with pytest.raises(ProjectGraphError):
        _edge("unsupported", "x", "causes", "y")  # type: ignore[arg-type]


def test_temporal_cutoff_and_caps_are_bounded_and_deterministic() -> None:
    temporal = _edge(
        "future",
        "a",
        ProjectRelationFamily.DEPENDS_ON,
        "b",
        valid_from="2026-08-26T00:00:00Z",
    )
    no_cutoff = build_project_graph(PROJECT, (temporal,))
    empty = build_project_graph(PROJECT, ())
    assert no_cutoff.stable_json() == empty.stable_json()

    fanout = tuple(
        _edge(f"fan-{index:02d}", "root", ProjectRelationFamily.DEPENDS_ON, f"n-{index:02d}")
        for index in range(4)
    )
    bounded = build_project_graph(PROJECT, fanout, fanout_cap=2, node_cap=5, edge_cap=2)
    assert len(bounded.edges) == 2
    assert bounded.truncated is True
    assert any(item.reason is GraphAbstentionReason.FANOUT_CAP for item in bounded.abstentions)


def test_one_and_two_hop_expansion_is_bounded_and_has_lineage() -> None:
    graph = build_project_graph(
        PROJECT,
        (
            _edge(
                "one",
                "a",
                ProjectRelationFamily.DEPENDS_ON,
                "b",
                provenance=("p1",),
                dependencies=("d1",),
            ),
            _edge(
                "two",
                "b",
                ProjectRelationFamily.IMPLEMENTS,
                "c",
                provenance=("p2",),
                dependencies=("d2",),
            ),
            _edge("three", "c", ProjectRelationFamily.TESTED_BY, "d"),
        ),
    )
    one = graph.expand_one_hop("a", direction=GraphDirection.OUTGOING)
    two = graph.expand_two_hop("a", direction=GraphDirection.OUTGOING)
    assert one.node_ids == ("a", "b")
    assert two.node_ids == ("a", "b", "c")
    assert two.provenance_ids == ("p1", "p2")
    assert two.dependency_ids == ("d1", "d2")
    missing = graph.expand_two_hop("missing")
    assert missing.outcome == "abstained"
    assert missing.abstention_reason is GraphAbstentionReason.NODE_NOT_FOUND


def test_duplicate_replay_is_idempotent_and_distinct_lineage_merges() -> None:
    first = _edge(
        "one",
        "a",
        ProjectRelationFamily.DEPENDS_ON,
        "b",
        provenance=("p1",),
    )
    replayed = build_project_graph(PROJECT, (first, first), as_of=AS_OF)
    baseline = build_project_graph(PROJECT, (first,), as_of=AS_OF)
    assert replayed.stable_json() == baseline.stable_json()

    second = replace(first, evidence_id="two", provenance_ids=("p2",))
    merged = build_project_graph(PROJECT, (second, first), as_of=AS_OF)
    assert len(merged.edges) == 1
    assert merged.edges[0].evidence_ids == ("one", "two")
    assert merged.edges[0].provenance_ids == ("p1", "p2")
    assert merged.nodes[0].evidence_ids == ("one", "two")


def test_conflicting_identifiers_and_relation_families_fail_closed() -> None:
    first = _edge("one", "a", ProjectRelationFamily.DEPENDS_ON, "b")
    reused = replace(first, object_id="c")
    conflict = build_project_graph(PROJECT, (first, reused), as_of=AS_OF)
    assert not conflict.edges
    assert any(
        item.reason is GraphAbstentionReason.DUPLICATE_CONFLICT for item in conflict.abstentions
    )

    other_family = replace(first, evidence_id="two", relation=ProjectRelationFamily.BLOCKS)
    ambiguous = build_project_graph(PROJECT, (first, other_family), as_of=AS_OF)
    assert not ambiguous.edges
    assert any(
        item.reason is GraphAbstentionReason.DUPLICATE_CONFLICT for item in ambiguous.abstentions
    )


def test_validation_rejects_prose_paths_structural_misuse_and_bool_hops() -> None:
    for bad_id in ("raw personal context", "café", "../private", r"C:\\private"):
        with pytest.raises(ProjectGraphError):
            _edge(bad_id, "a", ProjectRelationFamily.DEPENDS_ON, "b")
    with pytest.raises(ProjectGraphError, match="structural_relation_unsupported"):
        _edge(
            "structural",
            "a",
            ProjectRelationFamily.DEPENDS_ON,
            "b",
            basis=RelationBasis.STRUCTURAL,
        )

    graph = build_project_graph(
        PROJECT, (_edge("one", "a", ProjectRelationFamily.DEPENDS_ON, "b"),)
    )
    invalid = graph.expand("secret/path", hops=True)  # type: ignore[arg-type]
    assert invalid.outcome == "abstained"
    assert invalid.origin_id == "invalid-origin"
    assert "secret/path" not in str(invalid.to_dict())
