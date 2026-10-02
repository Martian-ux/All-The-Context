"""Pure, Core-only planning for a bounded affirmative user decision subset.

Call with transaction-resolved binding and server-read predecessor evidence.
This module neither authenticates receipts nor admits or mutates memory. A
binding identifies capture origin, not truth, outcomes or wider authority.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from .memory_policy import LiveUserClaim, ObservationOrigin
from .models import MemoryTruthRecordOut, MemoryTruthStatus, TruthConflictState
from .project_binding import BINDING_SCHEMA, ResolvedProjectContext
from .secret_boundary import contains_secret_like_text

PROJECT_DECISION_SCHEMA = "project-decision-v1"
MAX_PROJECT_DECISION_CHARS = 512

# This is a technical choice subset, not an open-ended value extractor. Neither
# identities nor fixture labels influence these vocabularies.
_ROLE_ALIASES = MappingProxyType(
    {
        "database": "database",
        "storage": "database",
        "offline cache": "offline_cache",
        "implementation language": "implementation_language",
        "programming language": "implementation_language",
        "frontend framework": "frontend_framework",
        "api format": "api_format",
        "api style": "api_format",
        "test runner": "test_runner",
        "package manager": "package_manager",
    }
)
_VALUES = MappingProxyType(
    {
        "database": {
            "sqlite": "SQLite",
            "postgresql": "PostgreSQL",
            "postgres": "PostgreSQL",
            "duckdb": "DuckDB",
        },
        "offline_cache": {"sqlite": "SQLite", "duckdb": "DuckDB"},
        "implementation_language": {
            "python": "Python",
            "typescript": "TypeScript",
            "javascript": "JavaScript",
            "rust": "Rust",
            "go": "Go",
        },
        "frontend_framework": {"react": "React", "vue": "Vue", "svelte": "Svelte"},
        "api_format": {"rest": "REST", "graphql": "GraphQL"},
        "test_runner": {"pytest": "pytest", "vitest": "Vitest", "jest": "Jest"},
        "package_manager": {"npm": "npm", "pnpm": "pnpm", "yarn": "Yarn", "uv": "uv"},
    }
)
_ROLE = "(?:" + "|".join(re.escape(value) for value in _ROLE_ALIASES) + ")"
_ATOM = r"[a-z]+"
_CHOICE = r"(?:use|i choose|i have decided to use|i've decided to use)"
_PATTERNS = (
    re.compile(
        rf"(?:for|in) this project,? {_CHOICE} (?P<value>{_ATOM}) "
        rf"(?:for|as) (?:the )?(?P<role>{_ROLE})"
    ),
    re.compile(
        rf"(?:i choose|i have decided to use|i've decided to use) (?P<value>{_ATOM}) "
        rf"(?:for|as) this project's (?P<role>{_ROLE})"
    ),
    re.compile(
        rf"(?:for|in) this project,? (?:my decision is to use|my choice is) "
        rf"(?P<value>{_ATOM}) (?:for|as) (?:the )?(?P<role>{_ROLE})"
    ),
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SOURCE_REFERENCE = re.compile(r"client-capture-formation-[0-9a-f]{64}")
_CORRECTION = re.compile(r"correction: (?P<statement>.+) instead of (?P<old>[a-z]+)")


def normalized_bound_project_name(value: object) -> str | None:
    """A bounded exact display-name condition, never project identity or authority."""
    if not isinstance(value, str) or _CONTROL.search(value) or len(value) > 80:
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:[ -][a-z][a-z0-9]*){0,3}", normalized):
        return None
    if set(re.split(r"[ -]", normalized)) & {
        "and",
        "or",
        "not",
        "no",
        "never",
        "maybe",
        "perhaps",
        "might",
        "if",
        "unless",
        "this",
        "that",
        "other",
        "its",
        "their",
        "should",
        "use",
    }:
        return None
    return normalized


def _named_patterns(name: str) -> tuple[re.Pattern[str], ...]:
    mention = re.escape(name)
    return (
        re.compile(
            rf"(?:i decided|i have decided|i've decided) {mention} should use "
            rf"(?P<value>{_ATOM}) for (?:its|the) (?P<role>{_ROLE})"
        ),
        re.compile(
            rf"for {mention},? {_CHOICE} (?P<value>{_ATOM}) "
            rf"(?:for|as) (?:the )?(?P<role>{_ROLE})"
        ),
        re.compile(
            rf"(?:i choose|i have decided to use|i've decided to use) "
            rf"(?P<value>{_ATOM}) (?:for|as) {mention}'s (?P<role>{_ROLE})"
        ),
    )


def is_project_decision_attempt(content: str) -> bool:
    """Identify technical/project wording solely to block legacy fallback.

    This never forms a claim or assigns a project. Unsupported role/value,
    historical or mixed wording must not gain authority through workflow parsing.
    Personal facets are handled separately by the caller.
    """

    text = " ".join(unicodedata.normalize("NFKC", content).casefold().split())
    return bool(
        re.search(r"\b(?:use|choose|choice|decided|decision)\b", text)
        and (
            re.search(rf"\b{_ROLE}\b", text)
            or re.search(r"\b(?:this|our|other|the other) project\b", text)
        )
    )


class ProjectDecisionAbstention(StrEnum):
    UNBOUND = "project_decision_unbound"
    NON_USER = "project_decision_non_user"
    UNSUPPORTED = "project_decision_unsupported_statement"
    SECRET = "project_decision_secret_like"
    PREDECESSOR = "project_decision_predecessor_unavailable"
    LINEAGE = "project_decision_lineage_mismatch"
    OLD_VALUE = "project_decision_old_value_mismatch"
    STALE_CORRECTION = "project_decision_stale_correction"


@dataclass(frozen=True, slots=True)
class ProjectDecisionPlan:
    """One user decision and its Core-derived capture partition, without authority."""

    claim: LiveUserClaim
    project_context: ResolvedProjectContext
    bound_project_name: str | None = None

    @property
    def entity_key(self) -> str:
        return "project-decision:" + self.project_context.canonical_project_id

    @property
    def scopes(self) -> tuple[str, ...]:
        return (self.project_context.project_scope,)


def plan_project_decision(
    content: str,
    *,
    role: str,
    project_context: ResolvedProjectContext | None,
    bound_project_name: object = None,
) -> ProjectDecisionPlan | ProjectDecisionAbstention:
    """Recognize a complete affirmative current-project decision or abstain.

    Do not pass caller-authored context or use this result as authenticated
    admission. Exact raw content remains in the claim; normalization is only
    for grammar matching. Whole-turn matching rejects negation, qualification,
    attribution, mixed turns, outcomes and history without substring extraction.
    """

    if role != "user":
        return ProjectDecisionAbstention.NON_USER
    if project_context is None:
        return ProjectDecisionAbstention.UNBOUND
    if not content or len(content) > MAX_PROJECT_DECISION_CHARS or _CONTROL.search(content):
        return ProjectDecisionAbstention.UNSUPPORTED
    if contains_secret_like_text(content):
        return ProjectDecisionAbstention.SECRET
    text = " ".join(unicodedata.normalize("NFKC", content).casefold().split())
    text = text.replace("\u2019", "'")
    # One optional period; never strip questions, ellipses or multiple sentences.
    if text.endswith("."):
        text = text[:-1]
    old = None
    correction = _CORRECTION.fullmatch(text)
    if correction is not None:
        text = correction.group("statement")
        old = correction.group("old")
    name = normalized_bound_project_name(bound_project_name)
    patterns = _PATTERNS + (_named_patterns(name) if name is not None else ())
    match = next((matched for pattern in patterns if (matched := pattern.fullmatch(text))), None)
    if match is None:
        return ProjectDecisionAbstention.UNSUPPORTED
    attribute = _ROLE_ALIASES[match.group("role")]
    value = _VALUES[attribute].get(match.group("value"))
    old_value = _VALUES[attribute].get(old) if old is not None else None
    if value is None or (old is not None and (old_value is None or old_value == value)):
        return ProjectDecisionAbstention.UNSUPPORTED
    structured = {
        "claim_type": "project_decision",
        "decision_schema": PROJECT_DECISION_SCHEMA,
        "decision_subject": attribute,
        "value": value,
    }
    if old_value is not None:
        structured["expected_old_value"] = old_value
    return ProjectDecisionPlan(
        claim=LiveUserClaim(
            kind="project_decision",
            content=content,
            value=value,
            attribute_key=attribute,
            structured_value=structured,
        ),
        project_context=project_context,
        bound_project_name=name,
    )


def check_project_decision_predecessor(
    plan: ProjectDecisionPlan,
    *,
    truth: MemoryTruthRecordOut,
    captured_binding: dict[str, object] | None,
    authorized: bool,
    as_of: datetime,
    observed_at: datetime,
) -> dict[str, str] | ProjectDecisionAbstention:
    """Check server-read same-scope source lineage and a correction's old value.

    The caller must select unique current truth, check tombstones and ACLs, and
    load this exact record's committed capture receipt in the write transaction.
    A dict's shape cannot attest that identity or make this a wire-level gate.
    Ordinary reinforcement returns no CAS fields to keep semantic identity stable.
    """

    record = truth.record
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return ProjectDecisionAbstention.PREDECESSOR
    timestamp = as_of.astimezone(UTC)
    if (
        not authorized
        or truth.status is not MemoryTruthStatus.CURRENT
        or record.status is not MemoryTruthStatus.CURRENT
        or truth.conflict_state is not TruthConflictState.NONE
        or truth.superseded_by
        or record.deleted_at is not None
        or (truth.source is not None and truth.source.deleted_at is not None)
        or (record.valid_from is not None and datetime.fromisoformat(record.valid_from) > timestamp)
        or (
            record.expires_at is not None and datetime.fromisoformat(record.expires_at) <= timestamp
        )
    ):
        return ProjectDecisionAbstention.PREDECESSOR
    if (
        record.kind != "project_decision"
        or record.entity_key != plan.entity_key
        or record.attribute_key != plan.claim.attribute_key
        or record.scopes != list(plan.scopes)
        or record.source_id is not None
        or record.source_type != "client_capture_formation"
        or record.source_service != "allthecontext-core"
        or record.source_reference is None
        or _SOURCE_REFERENCE.fullmatch(record.source_reference) is None
        or record.observation_origin != ObservationOrigin.LIVE_USER_EVIDENCE.value
        or captured_binding is None
        or set(captured_binding)
        != {"schema", "state", "canonical_project_id", "binding_id", "version"}
        or captured_binding.get("schema") != BINDING_SCHEMA
        or captured_binding.get("state") != "bound"
        or captured_binding.get("canonical_project_id") != plan.project_context.canonical_project_id
        or not isinstance(captured_binding.get("binding_id"), str)
        or not captured_binding.get("binding_id")
        or type(captured_binding.get("version")) is not int
        or captured_binding.get("version") != 1
    ):
        return ProjectDecisionAbstention.LINEAGE
    previous = plan_project_decision(
        record.content,
        role="user",
        project_context=plan.project_context,
        bound_project_name=plan.bound_project_name,
    )
    if not isinstance(previous, ProjectDecisionPlan):
        return ProjectDecisionAbstention.LINEAGE
    structured = record.structured_value or {}
    if previous.claim.attribute_key != plan.claim.attribute_key or any(
        structured.get(key) != value
        for key, value in previous.claim.structured_value.items()
        if key != "expected_old_value"
    ):
        return ProjectDecisionAbstention.LINEAGE
    expected_old = plan.claim.structured_value.get("expected_old_value")
    if expected_old is None:
        return {}
    if previous.claim.value != expected_old:
        return ProjectDecisionAbstention.OLD_VALUE
    # Fresh CAS fields cannot lend current authority to delayed old evidence,
    # including an ABA change where the old value has since become current again.
    # Equal-time corrections have no reliable order and abstain conservatively.
    if (
        observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or record.observed_at is None
        or observed_at > timestamp
        or observed_at <= datetime.fromisoformat(record.observed_at)
    ):
        return ProjectDecisionAbstention.STALE_CORRECTION
    return {"expected_record_id": record.id, "expected_record_version": str(record.version)}
