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
from typing import Final

from .ids import utc_now
from .models import MemoryTruthRecordOut, MemoryTruthStatus, TruthConflictState
from .project_continuity import (
    AssignmentOutcome,
    AuthorizedSourceBinding,
    ContinuityError,
    ProjectContextCapsule,
    ProjectContinuitySnapshot,
    ProjectEvidence,
    build_project_continuity,
    evidence_from_memory_truth,
)
from .storage import CoreStore

RUNTIME_TRUTH_PAGE_SIZE: Final = 500
RUNTIME_MAX_TRUTH_RECORDS: Final = 10_000
RUNTIME_MAX_PAGES: Final = RUNTIME_MAX_TRUTH_RECORDS // RUNTIME_TRUTH_PAGE_SIZE
RUNTIME_MAX_CAPSULE_CHARS: Final = 32_000
RUNTIME_MAX_CAPSULE_ITEMS: Final = 64

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
    """Use optional structured display metadata without copying archive text."""

    value = record.record.structured_value
    if not isinstance(value, Mapping):
        return None, ()
    name = None
    for key in ("project_name", "display_name", "name"):
        name = _safe_label(value.get(key))
        if name is not None:
            break
    raw_aliases = value.get("aliases")
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


def _read_truth(store: CoreStore) -> tuple[MemoryTruthRecordOut, ...]:
    records: list[MemoryTruthRecordOut] = []
    offset = 0
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
        records.extend(response.items)
        if len(records) > RUNTIME_MAX_TRUTH_RECORDS:
            raise ProjectRuntimeError("truth_projection_bound_exceeded")
        if not response.items or len(records) >= expected_total:
            return tuple(records)
        offset += len(response.items)
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
) -> ProjectContinuitySnapshot:
    """Build one in-memory project projection from bounded Core truth."""

    effective_as_of = as_of or utc_now()
    records = _read_truth(store)
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


__all__ = [
    "RUNTIME_MAX_CAPSULE_CHARS",
    "RUNTIME_MAX_CAPSULE_ITEMS",
    "RUNTIME_MAX_PAGES",
    "RUNTIME_MAX_TRUTH_RECORDS",
    "RUNTIME_TRUTH_PAGE_SIZE",
    "ProjectRuntimeError",
    "build_project_runtime",
    "capsule_for_project",
    "project_list_payload",
]
