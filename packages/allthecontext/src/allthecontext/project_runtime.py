"""Bounded Core-backed runtime projection for project continuity.

The project continuity compiler is intentionally storage-agnostic.  This
module is the small product seam that supplies it with one bounded snapshot of
Core Memory Truth.  It reads only public CoreStore projections, never writes a
capsule, and never opens an imported source or local-workspace sidecar.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal

from .ids import utc_now
from .models import MemoryTruthRecordOut, MemoryTruthStatus, TruthConflictState
from .project_continuity import (
    AssignmentOutcome,
    AuthorizedSourceBinding,
    ContinuityError,
    ProjectContextCapsule,
    ProjectContinuitySnapshot,
    ProjectEvidence,
    ProjectIdentity,
    build_project_continuity,
    evidence_from_memory_truth,
)
from .security import ClientPrincipal, record_is_allowed
from .storage import CoreStore

RUNTIME_TRUTH_PAGE_SIZE: Final = 500
RUNTIME_MAX_TRUTH_RECORDS: Final = 10_000
RUNTIME_MAX_PAGES: Final = RUNTIME_MAX_TRUTH_RECORDS // RUNTIME_TRUTH_PAGE_SIZE
RUNTIME_MAX_CAPSULE_CHARS: Final = 32_000
RUNTIME_MAX_CAPSULE_ITEMS: Final = 64
AMBIENT_PROJECT_SCHEMA: Final = "atc.ambient-project-context.v1"

_PROJECT_SCOPE_RE = re.compile(r"^project:(?P<ref>[^\s]{1,128})$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_REGISTERED_SOURCE_REFERENCE_RE = re.compile(r"^registered-source-item-[0-9a-f]{64}$")
_PROVIDER_LINEAGE_RE = re.compile(
    r"^[^#&\r\n]{1,1000}#conversation=(?P<conversation>[^&#\r\n]{1,200})"
    r"&message=(?P<message>[^&#\r\n]{1,200})$"
)
_IMPORTED_INSTRUCTION_KINDS = frozenset(
    {
        "command",
        "custom_instruction",
        "developer",
        "developer_message",
        "instruction",
        "instructions",
        "open_task",
        "prompt",
        "project_instruction",
        "system",
        "system_prompt",
        "task",
    }
)


class ProjectRuntimeError(RuntimeError):
    """A bounded fail-closed runtime projection error."""


AmbientProjectOutcome = Literal["activated", "abstained"]
AmbientProjectReason = Literal[
    "explicit_project_match",
    "host_project_match",
    "task_project_match",
    "single_authorized_project",
    "no_authorized_project_context",
    "invalid_project_signal",
    "project_signal_not_found",
    "ambiguous_project_signal",
    "ambiguous_task_match",
    "multiple_projects_without_match",
    "project_projection_unavailable",
]


@dataclass(frozen=True, slots=True)
class AmbientProjectActivation:
    """One content-bounded automatic project decision for an authorized caller."""

    outcome: AmbientProjectOutcome
    reason: AmbientProjectReason
    snapshot_revision: str | None
    capsule: ProjectContextCapsule | None = None
    schema: Literal["atc.ambient-project-context.v1"] = AMBIENT_PROJECT_SCHEMA

    def to_dict(self) -> dict[str, object]:
        capsule = self.capsule
        return {
            "schema": self.schema,
            "outcome": self.outcome,
            "reason": self.reason,
            "project_id": capsule.project_id if capsule is not None else None,
            "project_name": capsule.project_name if capsule is not None else None,
            "snapshot_revision": self.snapshot_revision,
            "capsule": capsule.to_dict() if capsule is not None else None,
        }


@dataclass(frozen=True, slots=True)
class _Anchor:
    record: MemoryTruthRecordOut
    binding: AuthorizedSourceBinding
    project_ref: str
    lineage: tuple[str, str] | None


def _opaque_ref(prefix: str, material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _project_scope(record: MemoryTruthRecordOut) -> tuple[str | None, bool]:
    """Return one exact project scope, or mark project-like scope ambiguity."""

    scopes = tuple(record.record.scopes)
    project_scopes = tuple(scope for scope in scopes if scope.startswith("project:"))
    if not project_scopes:
        return None, False
    if len(project_scopes) != 1 or _PROJECT_SCOPE_RE.fullmatch(project_scopes[0]) is None:
        return None, True
    return _opaque_ref("project-ref", f"project-scope-v1\0{project_scopes[0]}"), False


def _provider_lineage(record: MemoryTruthRecordOut) -> tuple[str, str] | None:
    """Parse only the provider-ingestion conversation reference grammar."""

    source_id = record.record.source_id
    source_type = record.record.source_type
    if record.source is not None:
        source_type = source_type or record.source.source_type
    source_reference = record.record.source_reference
    if source_id is None or source_type != "provider_archive" or source_reference is None:
        return None
    match = _PROVIDER_LINEAGE_RE.fullmatch(source_reference)
    if match is None:
        return None
    return source_id, match.group("conversation")


def _safe_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized or len(normalized) > 160 or "/" in normalized or "\\" in normalized:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


def _anchor_labels(record: MemoryTruthRecordOut) -> tuple[str | None, tuple[str, ...]]:
    """Derive one safe local display label without exposing source metadata."""

    value = record.record.structured_value
    name = None
    raw_aliases: object = None
    if isinstance(value, Mapping):
        for key in ("project_name", "display_name", "name"):
            name = _safe_label(value.get(key))
            if name is not None:
                break
        raw_aliases = value.get("aliases")
    if name is None:
        # Provider imports store a cleaned, explicit project statement as the
        # canonical Core record content. It is already visible in the local
        # admin capsule; using it here avoids unusable "Unnamed project" rows
        # while still rejecting paths, control characters, and oversized text.
        name = _safe_label(record.record.content)
    aliases = (
        tuple(
            sorted(
                {
                    label
                    for item in raw_aliases[:32]
                    if (label := _safe_label(item)) is not None and label != name
                }
            )
        )
        if isinstance(raw_aliases, (list, tuple))
        else ()
    )
    return name, aliases


def _binding_for_source(source_id: str) -> AuthorizedSourceBinding:
    opaque = _opaque_ref("binding", f"project-binding-source-v1\0{source_id}")
    workspace = _opaque_ref("workspace", f"project-workspace-source-v1\0{source_id}")
    return AuthorizedSourceBinding(
        binding_id=opaque,
        workspace_ref=workspace,
        source_id=source_id,
    )


def _binding_for_workspace(binding_hash: str) -> AuthorizedSourceBinding:
    opaque = _opaque_ref("binding", f"project-binding-workspace-v1\0{binding_hash}")
    workspace = _opaque_ref("workspace", f"project-workspace-workspace-v1\0{binding_hash}")
    return AuthorizedSourceBinding(binding_id=opaque, workspace_ref=workspace)


def _core_binding(vault_id: str) -> AuthorizedSourceBinding:
    opaque = _opaque_ref("binding", f"project-binding-core-v1\0{vault_id}")
    workspace = _opaque_ref("workspace", f"project-workspace-core-v1\0{vault_id}")
    return AuthorizedSourceBinding(binding_id=opaque, workspace_ref=workspace)


def _authorized_binding(
    record: MemoryTruthRecordOut,
    *,
    core_binding: AuthorizedSourceBinding,
    source_bindings: dict[str, AuthorizedSourceBinding],
) -> AuthorizedSourceBinding | None:
    source_id = record.record.source_id
    if source_id is None:
        if record.record.source_type == "registered_capture":
            structured = record.record.structured_value
            binding_hash = (
                structured.get("binding_hash") if isinstance(structured, Mapping) else None
            )
            if (
                record.record.observation_origin == "registered_source"
                and isinstance(structured, Mapping)
                and structured.get("schema") == "registered-source-fact-v1"
                and isinstance(binding_hash, str)
                and _HEX64_RE.fullmatch(binding_hash) is not None
                and _REGISTERED_SOURCE_REFERENCE_RE.fullmatch(record.record.source_reference or "")
            ):
                key = f"workspace:{binding_hash}"
                return source_bindings.setdefault(key, _binding_for_workspace(binding_hash))
            return None
        return core_binding
    source = record.source
    if (
        source is None
        or source.id != source_id
        or source.deleted_at is not None
        or source.import_status != "complete"
    ):
        return None
    return source_bindings.setdefault(source_id, _binding_for_source(source_id))


def _record_is_current_anchor(record: MemoryTruthRecordOut, *, as_of: str) -> bool:
    if (
        record.status is not MemoryTruthStatus.CURRENT
        or record.record.status is not MemoryTruthStatus.CURRENT
        or record.record.deleted_at is not None
        or record.conflict_state is not TruthConflictState.NONE
        or record.record.kind.casefold() not in {"project", "project_identity"}
        or not record.record.explicit_user_statement
        or (record.record.expires_at is not None and record.record.expires_at <= as_of)
    ):
        return False
    return not (record.source is not None and record.source.deleted_at is not None)


def _imported_instruction_like(evidence: ProjectEvidence) -> bool:
    return evidence.origin.value == "imported" and (
        evidence.kind in _IMPORTED_INSTRUCTION_KINDS or evidence.kind.endswith("_instruction")
    )


def _principal_allows_truth(
    value: MemoryTruthRecordOut,
    principal: ClientPrincipal | None,
) -> bool:
    if principal is None:
        return True
    return record_is_allowed(
        principal,
        set(value.record.scopes),
        set(value.record.allowed_clients),
        set(value.record.denied_clients),
    )


def _read_truth(
    store: CoreStore,
    *,
    principal: ClientPrincipal | None = None,
) -> tuple[MemoryTruthRecordOut, ...]:
    records: list[MemoryTruthRecordOut] = []
    offset = 0
    scanned = 0
    expected_total: int | None = None
    for _page in range(RUNTIME_MAX_PAGES):
        response = store.list_memory_truth(
            limit=RUNTIME_TRUTH_PAGE_SIZE,
            offset=offset,
        )
        if expected_total is None:
            expected_total = response.total
            if expected_total > RUNTIME_MAX_TRUTH_RECORDS:
                raise ProjectRuntimeError("truth_projection_bound_exceeded")
        if response.total != expected_total:
            raise ProjectRuntimeError("truth_projection_changed_during_read")
        page_items = tuple(response.items)
        records.extend(item for item in page_items if _principal_allows_truth(item, principal))
        scanned += len(page_items)
        if len(records) > RUNTIME_MAX_TRUTH_RECORDS:
            raise ProjectRuntimeError("truth_projection_bound_exceeded")
        if not page_items or scanned >= expected_total:
            return tuple(records)
        offset = scanned
    raise ProjectRuntimeError("truth_projection_page_bound_exceeded")


def _runtime_evidence(
    records: tuple[MemoryTruthRecordOut, ...],
    *,
    store: CoreStore,
    as_of: str,
) -> tuple[tuple[AuthorizedSourceBinding, ...], tuple[ProjectEvidence, ...]]:
    core_binding = _core_binding(store.vault_id())
    source_bindings: dict[str, AuthorizedSourceBinding] = {}
    binding_by_record: dict[str, AuthorizedSourceBinding | None] = {
        value.record.id: _authorized_binding(
            value,
            core_binding=core_binding,
            source_bindings=source_bindings,
        )
        for value in records
    }

    anchors: list[_Anchor] = []
    for value in records:
        binding = binding_by_record[value.record.id]
        if binding is None or not _record_is_current_anchor(value, as_of=as_of):
            continue
        anchor_project_ref, invalid_scope = _project_scope(value)
        if invalid_scope:
            continue
        if anchor_project_ref is None:
            anchor_project_ref = _opaque_ref(
                "project-ref",
                f"project-anchor-v1\0{binding.workspace_ref}\0{value.record.id}",
            )
        anchors.append(
            _Anchor(
                record=value,
                binding=binding,
                project_ref=anchor_project_ref,
                lineage=_provider_lineage(value),
            )
        )

    anchors_by_lineage: dict[tuple[str, str], list[_Anchor]] = defaultdict(list)
    for anchor_item in anchors:
        if anchor_item.lineage is not None:
            anchors_by_lineage[anchor_item.lineage].append(anchor_item)

    evidence_values: list[ProjectEvidence] = []
    anchor_by_id = {anchor.record.record.id: anchor for anchor in anchors}
    for value in records:
        record = value.record
        binding = binding_by_record[record.id]
        anchor_for_record = anchor_by_id.get(record.id)
        assigned_project_ref: str | None = (
            anchor_for_record.project_ref if anchor_for_record is not None else None
        )
        blocked = False
        ambiguous_lineage = False
        if anchor_for_record is None:
            assigned_project_ref, invalid_scope = _project_scope(value)
            if invalid_scope:
                blocked = True
            elif assigned_project_ref is None:
                lineage = _provider_lineage(value)
                lineage_anchors = anchors_by_lineage.get(lineage, ()) if lineage else ()
                if len(lineage_anchors) == 1:
                    assigned_project_ref = lineage_anchors[0].project_ref
                elif len(lineage_anchors) > 1:
                    ambiguous_lineage = len({item.project_ref for item in lineage_anchors}) > 1
                    blocked = not ambiguous_lineage
        try:
            evidence = evidence_from_memory_truth(
                value,
                binding_id=binding.binding_id if binding is not None else None,
                project_ref=assigned_project_ref,
            )
        except ContinuityError as error:
            raise ProjectRuntimeError("truth_record_not_project_safe") from error
        if anchor_for_record is not None:
            name, aliases = _anchor_labels(value)
            evidence = replace(evidence, name=name, aliases=aliases)
        if (blocked and not ambiguous_lineage) or _imported_instruction_like(evidence):
            evidence = replace(evidence, authorized=False)
        evidence_values.append(evidence)

    bindings = (core_binding, *tuple(source_bindings.values()))
    return bindings, tuple(evidence_values)


def build_project_runtime(
    store: CoreStore,
    *,
    as_of: str | None = None,
    character_budget: int = 12_000,
    item_budget: int = 32,
    principal: ClientPrincipal | None = None,
) -> ProjectContinuitySnapshot:
    """Build one in-memory project projection from bounded authorized Core truth."""

    effective_as_of = as_of or utc_now()
    records = _read_truth(store, principal=principal)
    bindings, evidence = _runtime_evidence(records, store=store, as_of=effective_as_of)
    return build_project_continuity(
        bindings,
        evidence,
        as_of=effective_as_of,
        character_budget=character_budget,
        item_budget=item_budget,
    )


def project_list_payload(snapshot: ProjectContinuitySnapshot) -> dict[str, object]:
    """Return the intentionally narrow admin project list contract."""

    items: list[dict[str, object]] = []
    for project in snapshot.projects:
        capsule = snapshot.capsule_for(project.project_id)
        items.append(
            {
                "project_id": project.project_id,
                "project_ref": project.project_ref,
                "name": project.name,
                "aliases": list(project.aliases),
                "item_count": (
                    len(capsule.items) + capsule.omitted_count if capsule is not None else 0
                ),
            }
        )
    return {
        "items": items,
        "total": len(items),
        "unresolved_count": sum(
            assignment.outcome is AssignmentOutcome.UNRESOLVED
            for assignment in snapshot.assignments
        ),
        "ambiguous_count": sum(
            assignment.outcome is AssignmentOutcome.AMBIGUOUS for assignment in snapshot.assignments
        ),
        "revision": snapshot.revision,
    }


def capsule_for_project(
    snapshot: ProjectContinuitySnapshot,
    project_id: str,
) -> ProjectContextCapsule:
    """Resolve one capsule while preserving the repository not-found behavior."""

    capsule = next(
        (item for item in snapshot.capsules if item.project_id == project_id),
        None,
    )
    if capsule is None:
        raise KeyError(project_id)
    return capsule


def _normalized_signal(value: str, *, maximum: int = 512) -> str | None:
    if type(value) is not str:
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized or len(normalized) > maximum:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized.casefold()


def _project_match_values(
    project_id: str,
    project_ref: str,
    name: str | None,
    aliases: tuple[str, ...],
) -> frozenset[str]:
    values = (project_id, project_ref, name, *aliases)
    return frozenset(
        normalized
        for value in values
        if value is not None and (normalized := _normalized_signal(value)) is not None
    )


def _task_mentions_label(task: str, label: str) -> bool:
    normalized_task = _normalized_signal(task, maximum=4_000)
    normalized_label = _normalized_signal(label)
    if normalized_task is None or normalized_label is None:
        return False
    # Very short display labels are too collision-prone for implicit activation.
    # They remain valid when a host supplies them as an explicit project signal.
    if len(re.sub(r"\W", "", normalized_label)) < 3:
        return False
    return (
        re.search(
            rf"(?<!\w){re.escape(normalized_label)}(?!\w)",
            normalized_task,
        )
        is not None
    )


def activate_project_context(
    snapshot: ProjectContinuitySnapshot,
    *,
    task_description: str = "",
    current_project: str | None = None,
    host_project_hint: str | None = None,
) -> AmbientProjectActivation:
    """Activate one authorized project without requiring an ATC user interface.

    The caller must build ``snapshot`` with the requesting principal. An explicit
    project signal wins. Otherwise a unique best-effort host display-name hint,
    a unique safe label in the task, or the sole content-bearing authorized
    project activates. Every ambiguous case abstains instead of guessing across
    projects.
    """

    eligible: list[tuple[ProjectIdentity, ProjectContextCapsule]] = []
    for project in snapshot.projects:
        capsule = snapshot.capsule_for(project.project_id)
        if not project.archived and capsule is not None and capsule.items:
            eligible.append((project, capsule))

    if not eligible:
        return AmbientProjectActivation(
            outcome="abstained",
            reason="no_authorized_project_context",
            snapshot_revision=snapshot.revision,
        )

    if current_project is not None:
        signal = _normalized_signal(current_project)
        if signal is None:
            return AmbientProjectActivation(
                outcome="abstained",
                reason="invalid_project_signal",
                snapshot_revision=snapshot.revision,
            )
        scope_signal = signal.removeprefix("project:")
        derived_scope_ref = _opaque_ref(
            "project-ref",
            f"project-scope-v1\0project:{scope_signal}",
        )
        matches = [
            capsule
            for project, capsule in eligible
            if (
                signal
                in _project_match_values(
                    project.project_id,
                    project.project_ref,
                    project.name,
                    project.aliases,
                )
                or project.project_ref == derived_scope_ref
            )
        ]
        if len(matches) == 1:
            return AmbientProjectActivation(
                outcome="activated",
                reason="explicit_project_match",
                snapshot_revision=snapshot.revision,
                capsule=matches[0],
            )
        return AmbientProjectActivation(
            outcome="abstained",
            reason=("ambiguous_project_signal" if len(matches) > 1 else "project_signal_not_found"),
            snapshot_revision=snapshot.revision,
        )

    host_signal = _normalized_signal(host_project_hint) if host_project_hint is not None else None
    if host_signal is not None:
        host_matches = [
            capsule
            for project, capsule in eligible
            if host_signal
            in _project_match_values(
                project.project_id,
                project.project_ref,
                project.name,
                project.aliases,
            )
        ]
        if len(host_matches) == 1:
            return AmbientProjectActivation(
                outcome="activated",
                reason="host_project_match",
                snapshot_revision=snapshot.revision,
                capsule=host_matches[0],
            )

    task_matches = [
        capsule
        for project, capsule in eligible
        if any(
            _task_mentions_label(task_description, label)
            for label in (project.name, *project.aliases)
            if label is not None
        )
    ]
    if len(task_matches) == 1:
        return AmbientProjectActivation(
            outcome="activated",
            reason="task_project_match",
            snapshot_revision=snapshot.revision,
            capsule=task_matches[0],
        )
    if len(task_matches) > 1:
        return AmbientProjectActivation(
            outcome="abstained",
            reason="ambiguous_task_match",
            snapshot_revision=snapshot.revision,
        )
    if len(eligible) == 1:
        return AmbientProjectActivation(
            outcome="activated",
            reason="single_authorized_project",
            snapshot_revision=snapshot.revision,
            capsule=eligible[0][1],
        )
    return AmbientProjectActivation(
        outcome="abstained",
        reason="multiple_projects_without_match",
        snapshot_revision=snapshot.revision,
    )


__all__ = [
    "AMBIENT_PROJECT_SCHEMA",
    "RUNTIME_MAX_CAPSULE_CHARS",
    "RUNTIME_MAX_CAPSULE_ITEMS",
    "RUNTIME_MAX_PAGES",
    "RUNTIME_MAX_TRUTH_RECORDS",
    "RUNTIME_TRUTH_PAGE_SIZE",
    "AmbientProjectActivation",
    "ProjectRuntimeError",
    "activate_project_context",
    "build_project_runtime",
    "capsule_for_project",
    "project_list_payload",
]
