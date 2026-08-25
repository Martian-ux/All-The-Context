"""Deterministic v0 project discovery and Project Context Capsules.

This module is deliberately a disposable, read-only boundary.  It accepts
authorized source bindings and already-sanitized evidence, then derives opaque
project identities, fail-closed assignments, and bounded capsules in memory.
It does not write to Core, create canonical records, interpret imported text as
instructions, or retain a second memory authority.

The public Memory Truth models are accepted as input through
``evidence_from_memory_truth``.  The adapter copies only the fields needed by
this projection; callers remain responsible for obtaining an authorized truth
view from Core.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from .models import (
    ContextRecordOut,
    MemoryTruthRecordOut,
    MemoryTruthStatus,
    ObservationDisposition,
    Sensitivity,
    TruthConflictState,
)
from .secret_boundary import contains_secret_like_text, contains_secret_like_value

PROJECT_ID_PREFIX = "project-"
PROJECT_ID_SCHEMA = "atc-project-identity-v0"
CAPSULE_SCHEMA: Literal["atc.project-context-capsule.v0"] = "atc.project-context-capsule.v0"
COMPILER_VERSION = "project-continuity-v0"
REDACTED_EVIDENCE = "[secret-like evidence omitted]"

MAX_REFERENCE_CHARS = 512
MAX_EVIDENCE_ID_CHARS = 256
MAX_CONTENT_CHARS = 16_000
MAX_LABEL_CHARS = 160
MAX_PROVENANCE_PER_EVIDENCE = 32
MAX_PROVENANCE_PER_ITEM = 8
MAX_CAPSULE_PROVENANCE = 64
MAX_CAPSULE_ITEMS = 64
MAX_CAPSULE_CHARS = 32_000
MAX_OMISSION_IDS = 16
PROJECT_ANCHOR_KINDS = frozenset({"project", "project_identity"})
IMPORTED_CAPSULE_KINDS = frozenset(
    {
        "goal",
        "objective",
        "current_goal",
        "project_decision",
        "decision",
        "architecture",
        "component",
        "constraint",
        "preference",
        "interaction_preference",
        "workflow",
        "blocker",
        "blocked",
        "recent_change",
        "completed_work",
        "test_outcome",
        "meaningful_change",
    }
)


class ContinuityError(ValueError):
    """A bounded contract error whose message does not echo supplied data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvidenceOrigin(StrEnum):
    """Trust boundary for evidence used by this derived projection."""

    USER = "user"
    CORE = "core"
    WORKSPACE = "workspace"
    IMPORTED = "imported"
    INFERRED = "inferred"


class EvidenceStatus(StrEnum):
    """Lifecycle states that can affect future projection influence."""

    CURRENT = "current"
    TENTATIVE = "tentative"
    SUPERSEDED = "superseded"
    CONFLICTED = "conflicted"
    UNAUTHORIZED = "unauthorized"
    EXPIRED = "expired"
    DELETED = "deleted"
    PURGED = "purged"


class AssignmentOutcome(StrEnum):
    """Project resolution outcome for one evidence item."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class CapsuleSection(StrEnum):
    """The narrow v0 user-useful continuity sections."""

    CURRENT_GOAL = "current_goal"
    DECISIONS = "decisions"
    CONSTRAINTS_PREFERENCES = "constraints_preferences"
    BLOCKERS = "blockers"
    RECENT_MEANINGFUL_CHANGES = "recent_meaningful_changes"


class OmissionReason(StrEnum):
    CHARACTER_BUDGET = "character_budget"
    ITEM_BUDGET = "item_budget"


class ProjectTransitionKind(StrEnum):
    """Explicit transition requests; transitions never move evidence here."""

    RENAME = "rename"
    MERGE = "merge"
    SPLIT = "split"
    ARCHIVE = "archive"


def _contract_text(value: object, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContinuityError("invalid_text")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized and not allow_empty:
        raise ContinuityError("empty_text")
    if len(normalized) > maximum:
        raise ContinuityError("text_too_long")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ContinuityError("control_character")
    return normalized


def _reference(value: object, *, maximum: int = MAX_REFERENCE_CHARS) -> str:
    normalized = _contract_text(value, maximum=maximum)
    if contains_secret_like_text(normalized):
        raise ContinuityError("secret_like_reference")
    return normalized


def _optional_reference(value: object | None) -> str | None:
    if value is None:
        return None
    return _reference(value)


def _references(values: Iterable[str], *, maximum: int) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ContinuityError("invalid_reference_list")
    normalized = tuple(_reference(value) for value in values)
    if len(normalized) > maximum:
        raise ContinuityError("too_many_references")
    if len(set(normalized)) != len(normalized):
        raise ContinuityError("duplicate_reference")
    return tuple(sorted(normalized))


def _timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _contract_text(value, maximum=100)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContinuityError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise ContinuityError("timestamp_requires_offset")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_content(value: object) -> tuple[str, bool]:
    content = _contract_text(value, maximum=MAX_CONTENT_CHARS)
    if contains_secret_like_text(content):
        return REDACTED_EVIDENCE, False
    return content, True


def _safe_label(value: object | None) -> str | None:
    if value is None:
        return None
    label = _contract_text(value, maximum=MAX_LABEL_CHARS)
    if contains_secret_like_text(label):
        return None
    return label


def _enum_value[T: StrEnum](value: T | str, enum_type: type[T], code: str) -> T:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ContinuityError(code) from exc


@dataclass(frozen=True, slots=True)
class AuthorizedSourceBinding:
    """A caller-authorized, opaque workspace/source binding.

    ``workspace_ref`` is a stable opaque root identity, not a filesystem path.
    ``source_id`` may point at an existing Core source record.  The module uses
    these fields only for exact membership and identity derivation; it never
    discovers paths or sources on its own.
    """

    binding_id: str
    workspace_ref: str
    source_id: str | None = None
    authorized: bool = True
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _reference(self.binding_id))
        object.__setattr__(self, "workspace_ref", _reference(self.workspace_ref))
        object.__setattr__(self, "source_id", _optional_reference(self.source_id))
        if type(self.authorized) is not bool or type(self.active) is not bool:
            raise ContinuityError("invalid_binding_state")

    @property
    def identity_material(self) -> str:
        """Stable opaque material used by the project-id hash."""

        return "\0".join((self.workspace_ref, self.binding_id, self.source_id or ""))


# The short name is convenient for callers that think in workspace terms.
WorkspaceBinding = AuthorizedSourceBinding


@dataclass(frozen=True, slots=True)
class ProjectEvidence:
    """One bounded evidence item supplied to the disposable projection.

    ``project_ref`` is a structured reference supplied by a trusted Core
    projection boundary; this module never parses it from ``content``.
    Current, explicit imported user memories may contribute as data, but only
    recognized project anchors can establish identity and instruction-like
    imported kinds never select a project.
    """

    evidence_id: str
    kind: str
    content: str
    binding_id: str | None = None
    project_ref: str | None = None
    origin: EvidenceOrigin = EvidenceOrigin.USER
    explicit: bool = False
    name: str | None = None
    aliases: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    record_id: str | None = None
    source_id: str | None = None
    observed_at: str | None = None
    expires_at: str | None = None
    status: EvidenceStatus = EvidenceStatus.CURRENT
    authorized: bool = True
    purged: bool = False
    sensitivity: Sensitivity = Sensitivity.NORMAL
    structured_value: Mapping[str, object] | None = None
    safe: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            _reference(self.evidence_id, maximum=MAX_EVIDENCE_ID_CHARS),
        )
        object.__setattr__(self, "kind", _contract_text(self.kind, maximum=128).casefold())
        content, content_safe = _safe_content(self.content)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "safe", type(self.safe) is bool and self.safe and content_safe)
        object.__setattr__(self, "binding_id", _optional_reference(self.binding_id))
        object.__setattr__(self, "project_ref", _optional_reference(self.project_ref))
        object.__setattr__(
            self, "origin", _enum_value(self.origin, EvidenceOrigin, "invalid_origin")
        )
        object.__setattr__(self, "name", _safe_label(self.name))
        aliases = tuple(
            label for label in (_safe_label(value) for value in self.aliases) if label is not None
        )
        object.__setattr__(self, "aliases", _references(aliases, maximum=16))
        object.__setattr__(
            self,
            "provenance_ids",
            _references(self.provenance_ids, maximum=MAX_PROVENANCE_PER_EVIDENCE),
        )
        object.__setattr__(self, "record_id", _optional_reference(self.record_id))
        object.__setattr__(self, "source_id", _optional_reference(self.source_id))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at))
        object.__setattr__(
            self,
            "status",
            _enum_value(self.status, EvidenceStatus, "invalid_evidence_status"),
        )
        if type(self.explicit) is not bool or type(self.authorized) is not bool:
            raise ContinuityError("invalid_evidence_state")
        if type(self.purged) is not bool:
            raise ContinuityError("invalid_evidence_state")
        if self.structured_value is not None:
            if not isinstance(self.structured_value, Mapping):
                raise ContinuityError("invalid_structured_value")
            if contains_secret_like_value(self.structured_value):
                object.__setattr__(self, "structured_value", None)
                object.__setattr__(self, "safe", False)

    @property
    def assignment_eligible(self) -> bool:
        """Whether this item may establish a project identity."""

        return (
            self.safe
            and self.authorized
            and not self.purged
            and self.status is EvidenceStatus.CURRENT
            and self.explicit
            and self.project_ref is not None
            and self.binding_id is not None
            and self.sensitivity is not Sensitivity.HIGHLY_SENSITIVE
            and (
                self.origin in {EvidenceOrigin.USER, EvidenceOrigin.CORE, EvidenceOrigin.WORKSPACE}
                or (self.origin is EvidenceOrigin.IMPORTED and self.kind in PROJECT_ANCHOR_KINDS)
            )
        )

    def is_expired(self, as_of: str | None) -> bool:
        if self.expires_at is None:
            return False
        if as_of is None:
            return True
        return self.expires_at <= as_of


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    """Opaque project identity plus conservative, explicit labels."""

    project_id: str
    project_ref: str
    workspace_ref: str
    binding_ids: tuple[str, ...]
    name: str | None = None
    aliases: tuple[str, ...] = ()
    archived: bool = False

    def __post_init__(self) -> None:
        project_id = _reference(self.project_id)
        if not project_id.startswith(PROJECT_ID_PREFIX):
            raise ContinuityError("invalid_project_id")
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "project_ref", _reference(self.project_ref))
        object.__setattr__(self, "workspace_ref", _reference(self.workspace_ref))
        object.__setattr__(self, "binding_ids", _references(self.binding_ids, maximum=64))
        object.__setattr__(self, "name", _safe_label(self.name))
        aliases = tuple(
            label for label in (_safe_label(value) for value in self.aliases) if label is not None
        )
        object.__setattr__(self, "aliases", _references(aliases, maximum=32))
        if type(self.archived) is not bool:
            raise ContinuityError("invalid_archive_state")

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "project_ref": self.project_ref,
            "workspace_ref": self.workspace_ref,
            "binding_ids": list(self.binding_ids),
            "name": self.name,
            "aliases": list(self.aliases),
            "archived": self.archived,
        }


@dataclass(frozen=True, slots=True)
class ProjectAssignment:
    """Fail-closed assignment result for one evidence item."""

    evidence_id: str
    outcome: AssignmentOutcome
    project_id: str | None = None
    candidate_project_ids: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _reference(self.evidence_id))
        object.__setattr__(
            self,
            "outcome",
            _enum_value(self.outcome, AssignmentOutcome, "invalid_assignment_outcome"),
        )
        object.__setattr__(self, "project_id", _optional_reference(self.project_id))
        object.__setattr__(
            self,
            "candidate_project_ids",
            _references(self.candidate_project_ids, maximum=16),
        )
        object.__setattr__(
            self, "reason", _contract_text(self.reason, maximum=160, allow_empty=True)
        )
        if self.outcome is AssignmentOutcome.RESOLVED:
            if self.project_id is None or self.candidate_project_ids:
                raise ContinuityError("invalid_resolved_assignment")
        elif self.project_id is not None:
            raise ContinuityError("invalid_unresolved_assignment")
        if self.outcome is AssignmentOutcome.AMBIGUOUS and len(self.candidate_project_ids) < 2:
            raise ContinuityError("invalid_ambiguous_assignment")

    @property
    def project_specific(self) -> bool:
        return self.outcome is AssignmentOutcome.RESOLVED


@dataclass(frozen=True, slots=True)
class ProjectTransitionInput:
    """A deterministic, confirmation-requiring transition proposal.

    This is intentionally an input/receipt shape only.  It contains no method
    that reassigns evidence, and ``evidence_policy`` is closed to retaining
    evidence in place until a separate Core-owned operation decides otherwise.
    """

    kind: ProjectTransitionKind
    from_project_ids: tuple[str, ...]
    to_project_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    from_name: str | None = None
    to_name: str | None = None
    rationale: str = ""
    requires_confirmation: bool = True
    evidence_policy: Literal["retain_in_place"] = "retain_in_place"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _enum_value(self.kind, ProjectTransitionKind, "invalid_transition_kind"),
        )
        object.__setattr__(self, "from_project_ids", _references(self.from_project_ids, maximum=16))
        object.__setattr__(self, "to_project_ids", _references(self.to_project_ids, maximum=16))
        object.__setattr__(self, "evidence_ids", _references(self.evidence_ids, maximum=64))
        object.__setattr__(self, "from_name", _safe_label(self.from_name))
        object.__setattr__(self, "to_name", _safe_label(self.to_name))
        object.__setattr__(
            self,
            "rationale",
            _contract_text(self.rationale, maximum=512, allow_empty=True),
        )
        if type(self.requires_confirmation) is not bool:
            raise ContinuityError("invalid_transition_confirmation")
        if self.evidence_policy != "retain_in_place":
            raise ContinuityError("invalid_transition_evidence_policy")
        if not self.from_project_ids:
            raise ContinuityError("transition_requires_source")
        if self.kind is ProjectTransitionKind.RENAME:
            if len(self.from_project_ids) != 1 or len(self.to_project_ids) != 1:
                raise ContinuityError("invalid_rename_transition")
            if self.from_project_ids != self.to_project_ids:
                raise ContinuityError("rename_changes_identity")
        elif self.kind is ProjectTransitionKind.MERGE and len(self.to_project_ids) != 1:
            raise ContinuityError("invalid_merge_transition")
        elif self.kind is ProjectTransitionKind.SPLIT and len(self.to_project_ids) < 2:
            raise ContinuityError("invalid_split_transition")
        elif self.kind is ProjectTransitionKind.ARCHIVE and self.to_project_ids:
            raise ContinuityError("invalid_archive_transition")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "from_project_ids": list(self.from_project_ids),
            "to_project_ids": list(self.to_project_ids),
            "evidence_ids": list(self.evidence_ids),
            "from_name": self.from_name,
            "to_name": self.to_name,
            "rationale": self.rationale,
            "requires_confirmation": self.requires_confirmation,
            "evidence_policy": self.evidence_policy,
        }


@dataclass(frozen=True, slots=True)
class CapsuleItem:
    """One selected, provenance-backed, sanitized capsule item."""

    evidence_id: str
    section: CapsuleSection
    text: str
    provenance_ids: tuple[str, ...]
    record_id: str | None = None
    source_id: str | None = None
    truncated: bool = False
    authority: Literal["current_memory", "workspace_fact"] = "current_memory"

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _reference(self.evidence_id))
        object.__setattr__(
            self,
            "section",
            _enum_value(self.section, CapsuleSection, "invalid_capsule_section"),
        )
        text, safe = _safe_content(self.text)
        if not safe:
            raise ContinuityError("secret_like_capsule_content")
        object.__setattr__(self, "text", text)
        object.__setattr__(
            self,
            "provenance_ids",
            _references(self.provenance_ids, maximum=MAX_PROVENANCE_PER_ITEM),
        )
        object.__setattr__(self, "record_id", _optional_reference(self.record_id))
        object.__setattr__(self, "source_id", _optional_reference(self.source_id))
        if type(self.truncated) is not bool:
            raise ContinuityError("invalid_truncation_state")
        if self.authority not in {"current_memory", "workspace_fact"}:
            raise ContinuityError("invalid_capsule_authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "section": self.section.value,
            "text": self.text,
            "provenance_ids": list(self.provenance_ids),
            "record_id": self.record_id,
            "source_id": self.source_id,
            "truncated": self.truncated,
            "authority": self.authority,
        }


@dataclass(frozen=True, slots=True)
class CapsuleOmission:
    """Truthful bounded accounting for candidates omitted by the budget."""

    reason: OmissionReason
    count: int
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason",
            _enum_value(self.reason, OmissionReason, "invalid_omission_reason"),
        )
        if type(self.count) is not int or self.count < 1:
            raise ContinuityError("invalid_omission_count")
        object.__setattr__(
            self,
            "evidence_ids",
            _references(self.evidence_ids, maximum=MAX_OMISSION_IDS),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason.value,
            "count": self.count,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class ProjectContextCapsule:
    """Immutable derived project briefing; never a canonical Memory Truth view."""

    project_id: str
    project_ref: str
    project_name: str | None
    aliases: tuple[str, ...]
    assignment_outcome: AssignmentOutcome
    current_goal: tuple[CapsuleItem, ...] = ()
    decisions: tuple[CapsuleItem, ...] = ()
    constraints_preferences: tuple[CapsuleItem, ...] = ()
    blockers: tuple[CapsuleItem, ...] = ()
    recent_meaningful_changes: tuple[CapsuleItem, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    character_budget: int = 12_000
    item_budget: int = 32
    used_chars: int = 0
    omitted_count: int = 0
    omissions: tuple[CapsuleOmission, ...] = ()
    truncated: bool = False
    abstention_reason: str | None = None
    schema: Literal["atc.project-context-capsule.v0"] = CAPSULE_SCHEMA
    compiler_version: str = COMPILER_VERSION
    derived_read_only: Literal[True] = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _reference(self.project_id))
        object.__setattr__(self, "project_ref", _reference(self.project_ref))
        object.__setattr__(self, "project_name", _safe_label(self.project_name))
        object.__setattr__(
            self,
            "aliases",
            _references(self.aliases, maximum=32),
        )
        object.__setattr__(
            self,
            "assignment_outcome",
            _enum_value(
                self.assignment_outcome,
                AssignmentOutcome,
                "invalid_capsule_assignment_outcome",
            ),
        )
        sections = (
            self.current_goal,
            self.decisions,
            self.constraints_preferences,
            self.blockers,
            self.recent_meaningful_changes,
        )
        if any(not isinstance(item, tuple) for item in sections):
            raise ContinuityError("invalid_capsule_items")
        all_items = tuple(item for section in sections for item in section)
        if any(not isinstance(item, CapsuleItem) for item in all_items):
            raise ContinuityError("invalid_capsule_items")
        if len(all_items) > MAX_CAPSULE_ITEMS:
            raise ContinuityError("too_many_capsule_items")
        object.__setattr__(
            self, "provenance_ids", _references(self.provenance_ids, maximum=MAX_CAPSULE_PROVENANCE)
        )
        object.__setattr__(
            self, "dependency_ids", _references(self.dependency_ids, maximum=MAX_CAPSULE_ITEMS)
        )
        if (
            type(self.character_budget) is not int
            or not 1 <= self.character_budget <= MAX_CAPSULE_CHARS
        ):
            raise ContinuityError("invalid_character_budget")
        if type(self.item_budget) is not int or not 1 <= self.item_budget <= MAX_CAPSULE_ITEMS:
            raise ContinuityError("invalid_item_budget")
        if len(all_items) > self.item_budget:
            raise ContinuityError("item_budget_exceeded")
        if type(self.used_chars) is not int or not 0 <= self.used_chars <= self.character_budget:
            raise ContinuityError("invalid_used_chars")
        if self.used_chars != sum(len(item.text) for item in all_items):
            raise ContinuityError("used_chars_mismatch")
        if type(self.omitted_count) is not int or self.omitted_count < 0:
            raise ContinuityError("invalid_omitted_count")
        if sum(item.count for item in self.omissions) != self.omitted_count:
            raise ContinuityError("omission_count_mismatch")
        if type(self.truncated) is not bool:
            raise ContinuityError("invalid_truncation_state")
        if (
            self.assignment_outcome is AssignmentOutcome.RESOLVED
            and self.abstention_reason is not None
        ):
            raise ContinuityError("resolved_capsule_abstention")
        if self.assignment_outcome is not AssignmentOutcome.RESOLVED and all_items:
            raise ContinuityError("abstained_capsule_has_items")
        object.__setattr__(
            self,
            "abstention_reason",
            _contract_text(self.abstention_reason, maximum=160) if self.abstention_reason else None,
        )

    @property
    def items(self) -> tuple[CapsuleItem, ...]:
        return (
            *self.current_goal,
            *self.decisions,
            *self.constraints_preferences,
            *self.blockers,
            *self.recent_meaningful_changes,
        )

    @property
    def injectable(self) -> bool:
        """Whether project-specific context may be injected."""

        return self.assignment_outcome is AssignmentOutcome.RESOLVED and not self.abstention_reason

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "compiler_version": self.compiler_version,
            "project_id": self.project_id,
            "project_ref": self.project_ref,
            "project_name": self.project_name,
            "aliases": list(self.aliases),
            "assignment_outcome": self.assignment_outcome.value,
            "sections": {
                "current_goal": [item.to_dict() for item in self.current_goal],
                "decisions": [item.to_dict() for item in self.decisions],
                "constraints_preferences": [
                    item.to_dict() for item in self.constraints_preferences
                ],
                "blockers": [item.to_dict() for item in self.blockers],
                "recent_meaningful_changes": [
                    item.to_dict() for item in self.recent_meaningful_changes
                ],
            },
            "provenance_ids": list(self.provenance_ids),
            "dependency_ids": list(self.dependency_ids),
            "character_budget": self.character_budget,
            "item_budget": self.item_budget,
            "used_chars": self.used_chars,
            "omitted_count": self.omitted_count,
            "omissions": [item.to_dict() for item in self.omissions],
            "truncated": self.truncated,
            "abstention_reason": self.abstention_reason,
            "derived_read_only": self.derived_read_only,
        }

    def stable_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ProjectContinuitySnapshot:
    """Complete in-memory result of one deterministic continuity rebuild."""

    projects: tuple[ProjectIdentity, ...]
    assignments: tuple[ProjectAssignment, ...]
    capsules: tuple[ProjectContextCapsule, ...]
    transitions: tuple[ProjectTransitionInput, ...] = ()
    revision: str = ""

    def __post_init__(self) -> None:
        if any(not isinstance(item, ProjectIdentity) for item in self.projects):
            raise ContinuityError("invalid_project_result")
        if any(not isinstance(item, ProjectAssignment) for item in self.assignments):
            raise ContinuityError("invalid_assignment_result")
        if any(not isinstance(item, ProjectContextCapsule) for item in self.capsules):
            raise ContinuityError("invalid_capsule_result")
        if any(not isinstance(item, ProjectTransitionInput) for item in self.transitions):
            raise ContinuityError("invalid_transition_result")
        revision = _reference(self.revision, maximum=64) if self.revision else ""
        if revision and (
            len(revision) != 64
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            raise ContinuityError("invalid_revision")
        object.__setattr__(self, "revision", revision)

    def capsule_for(self, project_id: str) -> ProjectContextCapsule | None:
        project_ref = _reference(project_id)
        return next(
            (capsule for capsule in self.capsules if capsule.project_id == project_ref), None
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "projects": [project.to_dict() for project in self.projects],
            "assignments": [
                {
                    "evidence_id": assignment.evidence_id,
                    "outcome": assignment.outcome.value,
                    "project_id": assignment.project_id,
                    "candidate_project_ids": list(assignment.candidate_project_ids),
                    "reason": assignment.reason,
                }
                for assignment in self.assignments
            ],
            "capsules": [capsule.to_dict() for capsule in self.capsules],
            "transitions": [transition.to_dict() for transition in self.transitions],
            "revision": self.revision,
        }


type PublicEvidence = ProjectEvidence | MemoryTruthRecordOut | ContextRecordOut


def derive_project_id(
    project_ref: str,
    bindings: Sequence[AuthorizedSourceBinding],
) -> str:
    """Derive an opaque ID from explicit project evidence and bindings.

    The project reference is required as structured evidence and is combined
    with the complete sorted authorized binding material.  A display name or
    content string alone can therefore never create a project identity.
    """

    normalized_ref = _reference(project_ref)
    if not bindings or any(
        not isinstance(binding, AuthorizedSourceBinding) for binding in bindings
    ):
        raise ContinuityError("project_identity_requires_binding")
    eligible = tuple(
        sorted(
            {
                binding.identity_material
                for binding in bindings
                if binding.authorized and binding.active
            }
        )
    )
    if not eligible:
        raise ContinuityError("project_identity_requires_authorized_binding")
    material = "\0".join((PROJECT_ID_SCHEMA, normalized_ref, *eligible))
    return PROJECT_ID_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _origin_from_public_record(record: ContextRecordOut) -> EvidenceOrigin:
    origin = (record.observation_origin or "").casefold()
    if origin in {"ongoing_client", "local_admin", "context_error", "legacy_migration"}:
        return EvidenceOrigin.USER
    if origin == "registered_source":
        return EvidenceOrigin.WORKSPACE
    if origin in {"archive_import", "provider_import", "relay_queue"}:
        return EvidenceOrigin.IMPORTED
    return EvidenceOrigin.CORE


def evidence_from_memory_truth(
    value: MemoryTruthRecordOut | ContextRecordOut,
    *,
    binding_id: str | None = None,
    project_ref: str | None = None,
    provenance_ids: Iterable[str] = (),
) -> ProjectEvidence:
    """Adapt a public Core truth/retrieval record without persisting it."""

    if isinstance(value, MemoryTruthRecordOut):
        record = value.record
        memory_status = value.status
        if record.status is not MemoryTruthStatus.CURRENT:
            memory_status = record.status
        if (
            value.conflict_state is not TruthConflictState.NONE
            or memory_status is MemoryTruthStatus.CONFLICTED
        ):
            evidence_status = EvidenceStatus.CONFLICTED
        elif value.superseded_by or memory_status is MemoryTruthStatus.SUPERSEDED:
            evidence_status = EvidenceStatus.SUPERSEDED
        elif memory_status is MemoryTruthStatus.TENTATIVE:
            evidence_status = EvidenceStatus.TENTATIVE
        elif memory_status is MemoryTruthStatus.DELETED or record.deleted_at is not None:
            evidence_status = EvidenceStatus.DELETED
        else:
            evidence_status = EvidenceStatus.CURRENT
        eligible_truth_evidence = tuple(
            item
            for item in value.evidence
            if item.disposition
            in {ObservationDisposition.APPLIED, ObservationDisposition.REINFORCED}
        )
        truth_provenance = tuple(
            item.observation_id for item in eligible_truth_evidence[:MAX_PROVENANCE_PER_EVIDENCE]
        )
        if value.evidence and not eligible_truth_evidence:
            evidence_status = EvidenceStatus.TENTATIVE
        source_deleted = value.source is not None and value.source.deleted_at is not None
        if source_deleted:
            evidence_status = EvidenceStatus.DELETED
        source_id = record.source_id or (value.source.id if value.source is not None else None)
        return ProjectEvidence(
            evidence_id=record.id,
            kind=record.kind,
            content=record.content,
            binding_id=binding_id,
            project_ref=project_ref,
            origin=_origin_from_public_record(record),
            explicit=record.explicit_user_statement,
            provenance_ids=(*truth_provenance, *tuple(provenance_ids)),
            record_id=record.id,
            source_id=source_id,
            observed_at=record.observed_at,
            expires_at=record.expires_at,
            status=evidence_status,
            authorized=True,
            sensitivity=record.sensitivity,
            structured_value=record.structured_value,
        )
    if isinstance(value, ContextRecordOut):
        evidence_status = (
            EvidenceStatus.CURRENT
            if value.status is MemoryTruthStatus.CURRENT and value.deleted_at is None
            else EvidenceStatus(value.status.value)
        )
        return ProjectEvidence(
            evidence_id=value.id,
            kind=value.kind,
            content=value.content,
            binding_id=binding_id,
            project_ref=project_ref,
            origin=_origin_from_public_record(value),
            explicit=value.explicit_user_statement,
            provenance_ids=tuple(provenance_ids),
            record_id=value.id,
            source_id=value.source_id,
            observed_at=value.observed_at,
            expires_at=value.expires_at,
            status=evidence_status,
            authorized=True,
            sensitivity=value.sensitivity,
            structured_value=value.structured_value,
        )
    raise ContinuityError("unsupported_public_evidence")


def _coerce_evidence(value: PublicEvidence) -> ProjectEvidence:
    if isinstance(value, ProjectEvidence):
        return value
    return evidence_from_memory_truth(value)


def _normalized_inputs(
    bindings: Sequence[AuthorizedSourceBinding],
    values: Sequence[PublicEvidence],
    purged_ids: Iterable[str],
) -> tuple[tuple[AuthorizedSourceBinding, ...], tuple[ProjectEvidence, ...]]:
    binding_values = tuple(bindings)
    if any(not isinstance(binding, AuthorizedSourceBinding) for binding in binding_values):
        raise ContinuityError("invalid_binding")
    binding_ids = tuple(binding.binding_id for binding in binding_values)
    if len(binding_ids) != len(set(binding_ids)):
        raise ContinuityError("duplicate_binding")
    purged = _references(purged_ids, maximum=MAX_PROVENANCE_PER_EVIDENCE * 4)
    source_bindings: dict[str, list[AuthorizedSourceBinding]] = defaultdict(list)
    for binding in binding_values:
        if binding.source_id is not None:
            source_bindings[binding.source_id].append(binding)
    evidence_values: list[ProjectEvidence] = []
    for raw in values:
        evidence = _coerce_evidence(raw)
        if evidence.binding_id is None and evidence.source_id is not None:
            matches = source_bindings.get(evidence.source_id, [])
            if len(matches) == 1:
                evidence = replace(evidence, binding_id=matches[0].binding_id)
        if (
            evidence.evidence_id in purged
            or (evidence.record_id is not None and evidence.record_id in purged)
            or (evidence.source_id is not None and evidence.source_id in purged)
        ):
            evidence = replace(evidence, status=EvidenceStatus.PURGED, purged=True)
        evidence_values.append(evidence)
    evidence_ids = tuple(item.evidence_id for item in evidence_values)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ContinuityError("duplicate_evidence")
    return (
        tuple(sorted(binding_values, key=lambda item: item.binding_id)),
        tuple(sorted(evidence_values, key=lambda item: item.evidence_id)),
    )


def _active_binding(
    evidence: ProjectEvidence,
    bindings_by_id: Mapping[str, AuthorizedSourceBinding],
) -> AuthorizedSourceBinding | None:
    binding = bindings_by_id.get(evidence.binding_id or "")
    if binding is None or not binding.authorized or not binding.active or not evidence.authorized:
        return None
    return binding


def _label_state(
    anchors: Sequence[ProjectEvidence],
) -> tuple[str | None, tuple[str, ...]]:
    eligible = tuple(
        evidence
        for evidence in anchors
        if evidence.explicit
        and (
            evidence.origin in {EvidenceOrigin.USER, EvidenceOrigin.CORE, EvidenceOrigin.WORKSPACE}
            or (
                evidence.origin is EvidenceOrigin.IMPORTED and evidence.kind in PROJECT_ANCHOR_KINDS
            )
        )
    )
    labels = {
        label
        for evidence in eligible
        for label in ((evidence.name,) if evidence.name is not None else ()) + evidence.aliases
    }
    if not labels:
        return None, ()
    named = tuple(
        (evidence.observed_at or "", evidence.evidence_id, evidence.name)
        for evidence in eligible
        if evidence.name is not None
    )
    current = max(named)[2] if named else min(labels)
    return current, tuple(sorted(labels - {current}))


def _discover_projects(
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[ProjectEvidence],
) -> tuple[tuple[ProjectIdentity, ...], dict[str, tuple[ProjectEvidence, ...]]]:
    bindings_by_id = {binding.binding_id: binding for binding in bindings}
    anchors_by_key: dict[tuple[str, str], list[ProjectEvidence]] = defaultdict(list)
    for item in evidence:
        binding = _active_binding(item, bindings_by_id)
        if binding is not None and item.assignment_eligible and item.project_ref is not None:
            anchors_by_key[(binding.workspace_ref, item.project_ref)].append(item)
    identities: list[ProjectIdentity] = []
    anchors_by_project: dict[str, tuple[ProjectEvidence, ...]] = {}
    for (workspace_ref, project_ref), anchors in sorted(anchors_by_key.items()):
        anchor_bindings_by_id = {
            item.binding_id: bindings_by_id[item.binding_id or ""]
            for item in anchors
            if item.binding_id in bindings_by_id
        }
        anchor_bindings = tuple(
            anchor_bindings_by_id[binding_id] for binding_id in sorted(anchor_bindings_by_id)
        )
        project_id = derive_project_id(project_ref, anchor_bindings)
        name, aliases = _label_state(anchors)
        identity = ProjectIdentity(
            project_id=project_id,
            project_ref=project_ref,
            workspace_ref=workspace_ref,
            binding_ids=tuple(binding.binding_id for binding in anchor_bindings),
            name=name,
            aliases=aliases,
        )
        identities.append(identity)
        anchors_by_project[project_id] = tuple(sorted(anchors, key=lambda item: item.evidence_id))
    return tuple(sorted(identities, key=lambda item: item.project_id)), anchors_by_project


def _assign_projects(
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[ProjectEvidence],
    projects: Sequence[ProjectIdentity],
    *,
    as_of: str | None,
    indexed: bool,
) -> tuple[ProjectAssignment, ...]:
    bindings_by_id = {binding.binding_id: binding for binding in bindings}
    project_by_key = {(project.workspace_ref, project.project_ref): project for project in projects}
    projects_by_workspace: dict[str, list[ProjectIdentity]] = defaultdict(list)
    for project in projects:
        projects_by_workspace[project.workspace_ref].append(project)
    if indexed:
        projects_by_workspace = {
            workspace: sorted(values, key=lambda item: item.project_id)
            for workspace, values in projects_by_workspace.items()
        }

    assignments: list[ProjectAssignment] = []
    for item in evidence:
        if (
            not item.safe
            or not item.authorized
            or item.purged
            or item.status is not EvidenceStatus.CURRENT
            or item.sensitivity is Sensitivity.HIGHLY_SENSITIVE
            or item.is_expired(as_of)
        ):
            assignments.append(
                ProjectAssignment(
                    item.evidence_id,
                    AssignmentOutcome.UNRESOLVED,
                    reason="evidence_not_currently_eligible",
                )
            )
            continue
        binding = _active_binding(item, bindings_by_id)
        if binding is None:
            assignments.append(
                ProjectAssignment(
                    item.evidence_id, AssignmentOutcome.UNRESOLVED, reason="binding_not_authorized"
                )
            )
            continue
        if item.origin is EvidenceOrigin.INFERRED:
            assignments.append(
                ProjectAssignment(
                    item.evidence_id,
                    AssignmentOutcome.UNRESOLVED,
                    reason="weak_inference_abstained",
                )
            )
            continue
        if (
            item.origin is EvidenceOrigin.IMPORTED
            and item.project_ref is not None
            and item.kind not in PROJECT_ANCHOR_KINDS
            and item.kind not in IMPORTED_CAPSULE_KINDS
        ):
            assignments.append(
                ProjectAssignment(
                    item.evidence_id,
                    AssignmentOutcome.UNRESOLVED,
                    reason="untrusted_imported_project_claim",
                )
            )
            continue
        if (
            item.project_ref is not None
            and (
                item.origin
                in {
                    EvidenceOrigin.USER,
                    EvidenceOrigin.CORE,
                    EvidenceOrigin.WORKSPACE,
                }
                or (
                    item.origin is EvidenceOrigin.IMPORTED
                    and item.kind in PROJECT_ANCHOR_KINDS | IMPORTED_CAPSULE_KINDS
                )
            )
            and item.explicit
        ):
            candidate_project = project_by_key.get((binding.workspace_ref, item.project_ref))
            if candidate_project is not None:
                assignments.append(
                    ProjectAssignment(
                        item.evidence_id,
                        AssignmentOutcome.RESOLVED,
                        project_id=candidate_project.project_id,
                        reason="explicit_project_evidence",
                    )
                )
                continue
            assignments.append(
                ProjectAssignment(
                    item.evidence_id,
                    AssignmentOutcome.UNRESOLVED,
                    reason="project_reference_not_discovered",
                )
            )
            continue
        # Imported prose never chooses a project by what it says. Any usable
        # ``project_ref`` above was supplied by the trusted runtime boundary;
        # without one, the authorized binding is the only basis for association.
        candidates = projects_by_workspace.get(binding.workspace_ref, [])
        if len(candidates) == 1:
            assignments.append(
                ProjectAssignment(
                    item.evidence_id,
                    AssignmentOutcome.RESOLVED,
                    project_id=candidates[0].project_id,
                    reason="single_authorized_workspace_binding",
                )
            )
        elif len(candidates) > 1:
            assignments.append(
                ProjectAssignment(
                    item.evidence_id,
                    AssignmentOutcome.AMBIGUOUS,
                    candidate_project_ids=tuple(project.project_id for project in candidates),
                    reason="multiple_projects_share_authorized_workspace",
                )
            )
        else:
            assignments.append(
                ProjectAssignment(
                    item.evidence_id, AssignmentOutcome.UNRESOLVED, reason="no_project_anchor"
                )
            )
    return tuple(sorted(assignments, key=lambda item: item.evidence_id))


_SECTION_BY_KIND: dict[str, CapsuleSection] = {
    "goal": CapsuleSection.CURRENT_GOAL,
    "objective": CapsuleSection.CURRENT_GOAL,
    "current_goal": CapsuleSection.CURRENT_GOAL,
    "project_decision": CapsuleSection.DECISIONS,
    "decision": CapsuleSection.DECISIONS,
    "architecture": CapsuleSection.DECISIONS,
    "component": CapsuleSection.DECISIONS,
    "constraint": CapsuleSection.CONSTRAINTS_PREFERENCES,
    "preference": CapsuleSection.CONSTRAINTS_PREFERENCES,
    "interaction_preference": CapsuleSection.CONSTRAINTS_PREFERENCES,
    "workflow": CapsuleSection.CONSTRAINTS_PREFERENCES,
    "blocker": CapsuleSection.BLOCKERS,
    "blocked": CapsuleSection.BLOCKERS,
    "recent_change": CapsuleSection.RECENT_MEANINGFUL_CHANGES,
    "completed_work": CapsuleSection.RECENT_MEANINGFUL_CHANGES,
    "test_outcome": CapsuleSection.RECENT_MEANINGFUL_CHANGES,
    "meaningful_change": CapsuleSection.RECENT_MEANINGFUL_CHANGES,
}


def _capsule_candidate(
    item: ProjectEvidence,
    *,
    as_of: str | None,
    bindings_by_id: Mapping[str, AuthorizedSourceBinding],
) -> CapsuleSection | None:
    binding = _active_binding(item, bindings_by_id)
    if (
        binding is None
        or not item.safe
        or item.status is not EvidenceStatus.CURRENT
        or item.purged
        or item.is_expired(as_of)
        or item.sensitivity is Sensitivity.HIGHLY_SENSITIVE
        or item.origin is EvidenceOrigin.INFERRED
        or (
            item.origin is EvidenceOrigin.IMPORTED
            and (not item.explicit or item.kind not in IMPORTED_CAPSULE_KINDS)
        )
    ):
        return None
    return _SECTION_BY_KIND.get(item.kind)


def _sort_candidates(
    section: CapsuleSection, values: Sequence[ProjectEvidence]
) -> tuple[ProjectEvidence, ...]:
    if section is CapsuleSection.RECENT_MEANINGFUL_CHANGES:
        return tuple(
            sorted(
                values, key=lambda item: (item.observed_at or "", item.evidence_id), reverse=True
            )
        )
    return tuple(
        sorted(
            values,
            key=lambda item: (
                int(item.explicit),
                item.observed_at or "",
                item.evidence_id,
            ),
            reverse=True,
        )
    )


def _bounded_text(content: str, remaining: int) -> tuple[str, bool]:
    if len(content) <= remaining:
        return content, False
    if remaining <= 0:
        return "", True
    suffix = "..."
    if remaining <= len(suffix):
        return suffix[:remaining], True
    return content[: remaining - len(suffix)] + suffix, True


def _provenance_for(item: ProjectEvidence) -> tuple[str, ...]:
    values: list[str] = []
    for value in (item.record_id, item.evidence_id, *item.provenance_ids):
        if value is not None and value not in values:
            values.append(value)
        if len(values) == MAX_PROVENANCE_PER_ITEM:
            break
    return tuple(values)


def _compile_capsule(
    project: ProjectIdentity,
    evidence: Sequence[ProjectEvidence],
    assignments: Sequence[ProjectAssignment],
    bindings: Sequence[AuthorizedSourceBinding],
    *,
    as_of: str | None,
    character_budget: int,
    item_budget: int,
) -> ProjectContextCapsule:
    if not 1 <= character_budget <= MAX_CAPSULE_CHARS:
        raise ContinuityError("invalid_character_budget")
    if not 1 <= item_budget <= MAX_CAPSULE_ITEMS:
        raise ContinuityError("invalid_item_budget")
    assignment_by_id = {assignment.evidence_id: assignment for assignment in assignments}
    bindings_by_id = {binding.binding_id: binding for binding in bindings}
    grouped: dict[CapsuleSection, list[ProjectEvidence]] = defaultdict(list)
    for item in evidence:
        assignment = assignment_by_id.get(item.evidence_id)
        if assignment is None or assignment.project_id != project.project_id:
            continue
        section = _capsule_candidate(item, as_of=as_of, bindings_by_id=bindings_by_id)
        if section is not None:
            grouped[section].append(item)

    ordered_candidates: list[tuple[CapsuleSection, ProjectEvidence]] = []
    section_order = (
        CapsuleSection.CURRENT_GOAL,
        CapsuleSection.DECISIONS,
        CapsuleSection.CONSTRAINTS_PREFERENCES,
        CapsuleSection.BLOCKERS,
        CapsuleSection.RECENT_MEANINGFUL_CHANGES,
    )
    for section in section_order:
        ordered_candidates.extend(
            (section, item) for item in _sort_candidates(section, grouped[section])
        )

    selected: dict[CapsuleSection, list[CapsuleItem]] = defaultdict(list)
    omitted_by_reason: dict[OmissionReason, list[ProjectEvidence]] = defaultdict(list)
    used_chars = 0
    truncated = False
    for index, (section, item) in enumerate(ordered_candidates):
        if index >= item_budget:
            omitted_by_reason[OmissionReason.ITEM_BUDGET].append(item)
            omitted_by_reason[OmissionReason.ITEM_BUDGET].extend(
                candidate for _, candidate in ordered_candidates[index + 1 :]
            )
            break
        remaining = character_budget - used_chars
        text, was_truncated = _bounded_text(item.content, remaining)
        if not text:
            omitted_by_reason[OmissionReason.CHARACTER_BUDGET].append(item)
            omitted_by_reason[OmissionReason.CHARACTER_BUDGET].extend(
                candidate for _, candidate in ordered_candidates[index + 1 :]
            )
            truncated = True
            break
        selected[section].append(
            CapsuleItem(
                evidence_id=item.evidence_id,
                section=section,
                text=text,
                provenance_ids=_provenance_for(item),
                record_id=item.record_id,
                source_id=item.source_id,
                truncated=was_truncated,
                authority="workspace_fact"
                if item.origin is EvidenceOrigin.WORKSPACE
                else "current_memory",
            )
        )
        used_chars += len(text)
        truncated = truncated or was_truncated
        if was_truncated:
            omitted_by_reason[OmissionReason.CHARACTER_BUDGET].extend(
                candidate for _, candidate in ordered_candidates[index + 1 :]
            )
            break

    omissions = tuple(
        CapsuleOmission(
            reason=reason,
            count=len(values),
            evidence_ids=tuple(item.evidence_id for item in values[:MAX_OMISSION_IDS]),
        )
        for reason, values in sorted(omitted_by_reason.items(), key=lambda pair: pair[0].value)
        if values
    )
    all_items = tuple(item for section in section_order for item in selected[section])
    provenance: list[str] = []
    dependencies: list[str] = []
    for selected_item in all_items:
        for value in (*selected_item.provenance_ids, selected_item.evidence_id):
            if value not in provenance and len(provenance) < MAX_CAPSULE_PROVENANCE:
                provenance.append(value)
        if selected_item.evidence_id not in dependencies and len(dependencies) < MAX_CAPSULE_ITEMS:
            dependencies.append(selected_item.evidence_id)
    return ProjectContextCapsule(
        project_id=project.project_id,
        project_ref=project.project_ref,
        project_name=project.name,
        aliases=project.aliases,
        assignment_outcome=AssignmentOutcome.RESOLVED,
        current_goal=tuple(selected[CapsuleSection.CURRENT_GOAL]),
        decisions=tuple(selected[CapsuleSection.DECISIONS]),
        constraints_preferences=tuple(selected[CapsuleSection.CONSTRAINTS_PREFERENCES]),
        blockers=tuple(selected[CapsuleSection.BLOCKERS]),
        recent_meaningful_changes=tuple(selected[CapsuleSection.RECENT_MEANINGFUL_CHANGES]),
        provenance_ids=tuple(provenance),
        dependency_ids=tuple(dependencies),
        character_budget=character_budget,
        item_budget=item_budget,
        used_chars=used_chars,
        omitted_count=sum(item.count for item in omissions),
        omissions=omissions,
        truncated=truncated or bool(omissions),
    )


def transition_inputs(
    transitions: Iterable[ProjectTransitionInput],
) -> tuple[ProjectTransitionInput, ...]:
    """Normalize explicit transition inputs without applying any of them."""

    values = tuple(transitions)
    if any(not isinstance(item, ProjectTransitionInput) for item in values):
        raise ContinuityError("invalid_transition")
    return tuple(
        sorted(
            values,
            key=lambda item: (
                item.kind.value,
                item.from_project_ids,
                item.to_project_ids,
                item.evidence_ids,
            ),
        )
    )


def _build_snapshot(
    bindings: Sequence[AuthorizedSourceBinding],
    values: Sequence[PublicEvidence],
    *,
    as_of: str | None,
    character_budget: int,
    item_budget: int,
    purged_ids: Iterable[str],
    transitions: Iterable[ProjectTransitionInput],
    indexed: bool,
) -> ProjectContinuitySnapshot:
    normalized_as_of = _timestamp(as_of)
    normalized_bindings, evidence = _normalized_inputs(bindings, values, purged_ids)
    projects, _anchors = _discover_projects(normalized_bindings, evidence)
    assignments = _assign_projects(
        normalized_bindings,
        evidence,
        projects,
        as_of=normalized_as_of,
        indexed=indexed,
    )
    normalized_transitions = transition_inputs(transitions)
    capsules = tuple(
        _compile_capsule(
            project,
            evidence,
            assignments,
            normalized_bindings,
            as_of=normalized_as_of,
            character_budget=character_budget,
            item_budget=item_budget,
        )
        for project in projects
    )
    material = {
        "projects": [project.to_dict() for project in projects],
        "assignments": [
            {
                "evidence_id": item.evidence_id,
                "outcome": item.outcome.value,
                "project_id": item.project_id,
                "candidate_project_ids": item.candidate_project_ids,
                "reason": item.reason,
            }
            for item in assignments
        ],
        "capsules": [capsule.to_dict() for capsule in capsules],
        "transitions": [item.to_dict() for item in normalized_transitions],
        "character_budget": character_budget,
        "item_budget": item_budget,
    }
    revision = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return ProjectContinuitySnapshot(
        projects=projects,
        assignments=assignments,
        capsules=capsules,
        transitions=normalized_transitions,
        revision=revision,
    )


def full_rebuild(
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[PublicEvidence],
    *,
    as_of: str | None = None,
    character_budget: int = 12_000,
    item_budget: int = 32,
    purged_ids: Iterable[str] = (),
    transitions: Iterable[ProjectTransitionInput] = (),
) -> ProjectContinuitySnapshot:
    """Clean-build oracle that scans every sanitized input in fixed order."""

    return _build_snapshot(
        bindings,
        evidence,
        as_of=as_of,
        character_budget=character_budget,
        item_budget=item_budget,
        purged_ids=purged_ids,
        transitions=transitions,
        indexed=False,
    )


def optimized_rebuild(
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[PublicEvidence],
    *,
    as_of: str | None = None,
    character_budget: int = 12_000,
    item_budget: int = 32,
    purged_ids: Iterable[str] = (),
    transitions: Iterable[ProjectTransitionInput] = (),
) -> ProjectContinuitySnapshot:
    """Indexed rebuild path; it must remain equal to ``full_rebuild``."""

    return _build_snapshot(
        bindings,
        evidence,
        as_of=as_of,
        character_budget=character_budget,
        item_budget=item_budget,
        purged_ids=purged_ids,
        transitions=transitions,
        indexed=True,
    )


def discover_projects(
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[PublicEvidence],
    *,
    purged_ids: Iterable[str] = (),
) -> ProjectContinuitySnapshot:
    """Convenience discovery-only call with no capsule content budget pressure."""

    return full_rebuild(
        bindings,
        evidence,
        character_budget=MAX_CAPSULE_CHARS,
        item_budget=MAX_CAPSULE_ITEMS,
        purged_ids=purged_ids,
    )


def compile_project_capsule(
    project: ProjectIdentity,
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[ProjectEvidence],
    assignments: Sequence[ProjectAssignment],
    *,
    as_of: str | None = None,
    character_budget: int = 12_000,
    item_budget: int = 32,
) -> ProjectContextCapsule:
    """Compile one resolved project capsule without any storage side effect."""

    return _compile_capsule(
        project,
        tuple(evidence),
        tuple(assignments),
        tuple(bindings),
        as_of=_timestamp(as_of),
        character_budget=character_budget,
        item_budget=item_budget,
    )


def build_project_continuity(
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[PublicEvidence],
    **kwargs: Any,
) -> ProjectContinuitySnapshot:
    """Named façade used by integration callers for the optimized v0 path."""

    return optimized_rebuild(bindings, evidence, **kwargs)


def rebuild_project_continuity(
    bindings: Sequence[AuthorizedSourceBinding],
    evidence: Sequence[PublicEvidence],
    **kwargs: Any,
) -> ProjectContinuitySnapshot:
    """Named full-rebuild oracle façade."""

    return full_rebuild(bindings, evidence, **kwargs)


__all__ = [
    "CAPSULE_SCHEMA",
    "AssignmentOutcome",
    "AuthorizedSourceBinding",
    "CapsuleItem",
    "CapsuleOmission",
    "CapsuleSection",
    "ContinuityError",
    "EvidenceOrigin",
    "EvidenceStatus",
    "ProjectAssignment",
    "ProjectContextCapsule",
    "ProjectContinuitySnapshot",
    "ProjectEvidence",
    "ProjectIdentity",
    "ProjectTransitionInput",
    "ProjectTransitionKind",
    "WorkspaceBinding",
    "build_project_continuity",
    "compile_project_capsule",
    "derive_project_id",
    "discover_projects",
    "evidence_from_memory_truth",
    "full_rebuild",
    "optimized_rebuild",
    "rebuild_project_continuity",
    "transition_inputs",
]
