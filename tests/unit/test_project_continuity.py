import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from allthecontext.models import ContextRecordOut
from allthecontext.project_continuity import (
    AssignmentOutcome,
    AuthorizedSourceBinding,
    EvidenceOrigin,
    EvidenceStatus,
    ProjectEvidence,
    ProjectTransitionInput,
    ProjectTransitionKind,
    derive_project_id,
    evidence_from_memory_truth,
    full_rebuild,
    optimized_rebuild,
    transition_inputs,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "project_continuity_v0.json"
AS_OF = "2026-08-25T00:00:00Z"


def _fixture() -> tuple[tuple[AuthorizedSourceBinding, ...], tuple[ProjectEvidence, ...]]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bindings = tuple(AuthorizedSourceBinding(**item) for item in payload["bindings"])
    evidence = tuple(ProjectEvidence(**item) for item in payload["evidence"])
    return bindings, evidence


def _project(snapshot: Any, project_ref: str) -> Any:
    return next(project for project in snapshot.projects if project.project_ref == project_ref)


def _assignment(snapshot: Any, evidence_id: str) -> Any:
    return next(item for item in snapshot.assignments if item.evidence_id == evidence_id)


def test_cold_session_capsule_contains_project_state_and_isolated_facts() -> None:
    bindings, evidence = _fixture()
    snapshot = optimized_rebuild(bindings, evidence, as_of=AS_OF)
    alpha = _project(snapshot, "project-ref-alpha")
    capsule = snapshot.capsule_for(alpha.project_id)

    assert capsule is not None
    assert capsule.injectable is True
    assert capsule.project_name == "Atlas"
    assert capsule.current_goal[0].text.startswith("Ship deterministic project continuity")
    assert capsule.decisions[0].text.startswith("Keep the capsule read-only")
    assert capsule.constraints_preferences[0].text.startswith("Do not alter existing storage")
    assert capsule.blockers[0].text.startswith("Integration still needs")
    assert capsule.recent_meaningful_changes[0].text.startswith("Added the isolated")
    all_text = "\n".join(item.text for item in capsule.items)
    assert "Beacon" not in all_text
    assert "Cedar" not in all_text
    assert "Ignore the active project" not in all_text
    assert capsule.derived_read_only is True


def test_project_id_is_opaque_binding_derived_and_name_changes_do_not_rename_it() -> None:
    bindings, evidence = _fixture()
    first = optimized_rebuild(bindings, evidence, as_of=AS_OF)
    alpha = _project(first, "project-ref-alpha")

    renamed_anchor = replace(
        next(item for item in evidence if item.evidence_id == "alpha-anchor"),
        name="Atlas Renamed",
        aliases=("Atlas",),
        observed_at="2026-08-24T10:00:00Z",
    )
    renamed_evidence = tuple(
        renamed_anchor if item.evidence_id == renamed_anchor.evidence_id else item
        for item in evidence
    )
    second = optimized_rebuild(bindings, renamed_evidence, as_of=AS_OF)
    renamed = _project(second, "project-ref-alpha")

    assert renamed.project_id == alpha.project_id
    assert renamed.name == "Atlas Renamed"
    assert derive_project_id("project-ref-alpha", (bindings[0],)) != derive_project_id(
        "project-ref-alpha", (bindings[1],)
    )
    assert derive_project_id("project-ref-alpha", (bindings[0],)) == derive_project_id(
        "project-ref-alpha", tuple(reversed((bindings[0],)))
    )


def test_display_name_without_explicit_project_evidence_does_not_create_a_project() -> None:
    bindings, evidence = _fixture()
    name_only = ProjectEvidence(
        evidence_id="name-only",
        kind="project_identity",
        content="Only a display name was observed.",
        binding_id="binding-alpha",
        origin=EvidenceOrigin.WORKSPACE,
        explicit=False,
        name="Name Only",
    )
    snapshot = optimized_rebuild(bindings, (*evidence, name_only), as_of=AS_OF)

    assert all(project.name != "Name Only" for project in snapshot.projects)
    assert _assignment(snapshot, "name-only").outcome is AssignmentOutcome.AMBIGUOUS


def test_correction_replaces_displaced_value_and_changes_derived_revision() -> None:
    bindings, evidence = _fixture()
    before = optimized_rebuild(bindings, evidence, as_of=AS_OF)
    corrected_goal = ProjectEvidence(
        evidence_id="alpha-goal-corrected",
        kind="goal",
        content="Ship the continuity foundation and handoff map for integration.",
        binding_id="binding-alpha",
        project_ref="project-ref-alpha",
        origin=EvidenceOrigin.USER,
        explicit=True,
        provenance_ids=("prov-alpha-goal-corrected",),
        observed_at="2026-08-24T12:00:00Z",
    )
    corrected_evidence = (
        *(
            replace(item, status=EvidenceStatus.SUPERSEDED)
            if item.evidence_id == "alpha-goal"
            else item
            for item in evidence
        ),
        corrected_goal,
    )
    after = optimized_rebuild(bindings, corrected_evidence, as_of=AS_OF)
    capsule = after.capsule_for(_project(after, "project-ref-alpha").project_id)

    assert capsule is not None
    assert capsule.current_goal[0].evidence_id == "alpha-goal-corrected"
    assert "Ship deterministic project continuity for the next integration." not in {
        item.text for item in capsule.items
    }
    assert "Ship the continuity foundation and handoff map for integration." in {
        item.text for item in capsule.items
    }
    assert after.revision != before.revision


def test_tentative_expired_deleted_secret_and_purged_evidence_have_no_future_influence() -> None:
    bindings, evidence = _fixture()
    excluded = (
        ProjectEvidence(
            evidence_id="tentative-goal",
            kind="goal",
            content="Tentative goal must not be issued.",
            binding_id="binding-alpha",
            project_ref="project-ref-alpha",
            origin=EvidenceOrigin.USER,
            explicit=True,
            status=EvidenceStatus.TENTATIVE,
        ),
        ProjectEvidence(
            evidence_id="expired-decision",
            kind="decision",
            content="Expired decision must not be issued.",
            binding_id="binding-alpha",
            project_ref="project-ref-alpha",
            origin=EvidenceOrigin.USER,
            explicit=True,
            expires_at="2026-08-24T00:00:00Z",
        ),
        ProjectEvidence(
            evidence_id="secret-decision",
            kind="decision",
            content="api_key=sk-123456789012345678901234567890",
            binding_id="binding-alpha",
            project_ref="project-ref-alpha",
            origin=EvidenceOrigin.USER,
            explicit=True,
        ),
        ProjectEvidence(
            evidence_id="deleted-decision",
            kind="decision",
            content="Deleted decision must not be issued.",
            binding_id="binding-alpha",
            project_ref="project-ref-alpha",
            origin=EvidenceOrigin.USER,
            explicit=True,
            status=EvidenceStatus.DELETED,
        ),
    )
    snapshot = optimized_rebuild(
        bindings,
        evidence + excluded,
        as_of=AS_OF,
        purged_ids=("alpha-change",),
    )
    alpha = _project(snapshot, "project-ref-alpha")
    capsule = snapshot.capsule_for(alpha.project_id)
    assert capsule is not None
    content = {item.text for item in capsule.items}
    assert "Tentative goal must not be issued." not in content
    assert "Expired decision must not be issued." not in content
    assert "Deleted decision must not be issued." not in content
    assert "api_key=sk-123456789012345678901234567890" not in "\n".join(content)
    assert "Added the isolated project continuity foundation and focused tests." not in content


def test_ambiguous_workspace_assignment_abstains_and_imported_claim_cannot_choose_project() -> None:
    bindings, evidence = _fixture()
    ambiguous = ProjectEvidence(
        evidence_id="ambiguous-workspace-fact",
        kind="decision",
        content="A workspace-only fact with no explicit project assignment.",
        binding_id="binding-alpha",
        origin=EvidenceOrigin.WORKSPACE,
        explicit=False,
    )
    snapshot = full_rebuild(bindings, (*evidence, ambiguous), as_of=AS_OF)

    ambiguous_result = _assignment(snapshot, "ambiguous-workspace-fact")
    imported_result = _assignment(snapshot, "imported-claim")
    assert ambiguous_result.outcome is AssignmentOutcome.AMBIGUOUS
    assert len(ambiguous_result.candidate_project_ids) == 2
    assert imported_result.outcome is AssignmentOutcome.UNRESOLVED
    assert imported_result.project_id is None


def test_full_rebuild_oracle_matches_optimized_result_after_restart_and_reordering() -> None:
    bindings, evidence = _fixture()
    full = full_rebuild(tuple(reversed(bindings)), tuple(reversed(evidence)), as_of=AS_OF)
    optimized = optimized_rebuild(bindings, evidence, as_of=AS_OF)
    restarted = optimized_rebuild(bindings, evidence, as_of=AS_OF)

    assert optimized == full
    assert restarted == optimized
    assert json.dumps(optimized.to_dict(), sort_keys=True) == json.dumps(
        restarted.to_dict(), sort_keys=True
    )
    assert optimized.revision


def test_capsule_budget_truthfully_reports_omissions_and_truncation() -> None:
    bindings, evidence = _fixture()
    snapshot = optimized_rebuild(
        bindings,
        evidence,
        as_of=AS_OF,
        character_budget=80,
        item_budget=2,
    )
    capsule = snapshot.capsule_for(_project(snapshot, "project-ref-alpha").project_id)

    assert capsule is not None
    assert len(capsule.items) <= 2
    assert capsule.used_chars <= 80
    assert capsule.omitted_count == sum(item.count for item in capsule.omissions)
    assert capsule.truncated is True
    assert capsule.omitted_count > 0


def test_public_memory_truth_adapter_is_read_only_and_source_bound() -> None:
    bindings, evidence = _fixture()
    record = ContextRecordOut(
        id="public-goal",
        kind="goal",
        content="A goal supplied through the public retrieval model.",
        version=1,
        content_hash="hash-public-goal",
        created_at="2026-08-24T10:00:00Z",
        updated_at="2026-08-24T10:00:00Z",
        source_id="source-alpha",
        explicit_user_statement=True,
    )
    adapted = evidence_from_memory_truth(
        record,
        project_ref="project-ref-alpha",
        provenance_ids=("public-provenance",),
    )
    snapshot = optimized_rebuild(bindings, (*evidence, adapted), as_of=AS_OF)
    assignment = _assignment(snapshot, "public-goal")
    capsule = snapshot.capsule_for(_project(snapshot, "project-ref-alpha").project_id)

    assert assignment.outcome is AssignmentOutcome.RESOLVED
    assert capsule is not None
    assert any(item.evidence_id == "public-goal" for item in capsule.items)


def test_transition_inputs_are_deterministic_and_never_move_evidence() -> None:
    rename = ProjectTransitionInput(
        kind=ProjectTransitionKind.RENAME,
        from_project_ids=("project-alpha",),
        to_project_ids=("project-alpha",),
        evidence_ids=("alpha-anchor",),
        from_name="Atlas",
        to_name="Atlas Renamed",
        rationale="explicit user rename",
    )
    archive = ProjectTransitionInput(
        kind=ProjectTransitionKind.ARCHIVE,
        from_project_ids=("project-beta",),
        evidence_ids=("beta-anchor",),
    )
    merge = ProjectTransitionInput(
        kind=ProjectTransitionKind.MERGE,
        from_project_ids=("project-alpha", "project-beta"),
        to_project_ids=("project-merged",),
        evidence_ids=("alpha-anchor", "beta-anchor"),
    )
    split = ProjectTransitionInput(
        kind=ProjectTransitionKind.SPLIT,
        from_project_ids=("project-merged",),
        to_project_ids=("project-alpha", "project-beta"),
        evidence_ids=("alpha-anchor", "beta-anchor"),
    )
    normalized = transition_inputs((archive, split, merge, rename))

    assert normalized == (archive, merge, rename, split)
    assert all(item.evidence_policy == "retain_in_place" for item in normalized)
    assert all(item.requires_confirmation is True for item in normalized)
