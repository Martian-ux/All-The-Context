"""Deterministic automatic policy for context observations.

The policy never interprets imported text as instructions. Callers supply a
server-derived origin and the candidate's structured provenance; only Core
turns the resulting decision into current context.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from .models import (
    Availability,
    CandidateInput,
    ObservationDisposition,
    Sensitivity,
)
from .secret_boundary import contains_direct_secret
from .security import ClientPrincipal, principal_may_attest_explicit_user_statement

AUTOMATIC_POLICY_VERSION = "automatic-v1"

# Kinds where contradictory unkeyed historical statements must not all remain
# confident current truth. Keyed entity/attribute slots keep existing behavior.
UNKEYED_CONFLICT_KINDS = frozenset(
    {
        "preference",
        "interaction_preference",
        "editor_preference",
        "goal",
        "project",
        "project_decision",
        "workflow",
        "constraint",
    }
)


class ObservationOrigin(StrEnum):
    ONGOING_CLIENT = "ongoing_client"
    ARCHIVE_IMPORT = "archive_import"
    RELAY_QUEUE = "relay_queue"
    CONTEXT_ERROR = "context_error"
    LOCAL_ADMIN = "local_admin"
    LEGACY_MIGRATION = "legacy_migration"


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    mode: str = "automatic"
    sensitive_mode: str = "local_only"
    inference_mode: str = "corroborate"
    policy_version: str = AUTOMATIC_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    disposition: ObservationDisposition
    reason: str
    availability: Availability
    sensitivity: Sensitivity = Sensitivity.NORMAL


_SENSITIVITY_RANK = {
    Sensitivity.NORMAL: 0,
    Sensitivity.SENSITIVE: 1,
    Sensitivity.HIGHLY_SENSITIVE: 2,
}

_HIGHLY_SENSITIVE_HINT = re.compile(
    r"(?:"
    r"\b(?:social security(?: number)?|ssn)\b|"
    r"\b\d{3}-\d{2}-\d{4}\b|"
    r"\b(?:passport|driver'?s license)\b(?:\s*(?:number|no\.?|#))?|"
    r"\b(?:credit|debit)\s+card\b|"
    r"\brouting number\b"
    r")",
    flags=re.IGNORECASE,
)
_SENSITIVE_HINT = re.compile(
    r"(?:"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"\b(?:phone|mobile)\s+number\b|"
    r"\b(?:date of birth|dob|birthday)\b|"
    r"\b(?:diagnos(?:is|ed)|medication|prescription|therapist|chemotherapy|"
    r"pregnant|cancer|diabetes|asthma|depression|anxiety disorder|inhaler|"
    r"my doctor)\b|"
    r"\b(?:my (?:wife|husband|spouse|girlfriend|boyfriend|ex|kids?|children|"
    r"son|daughter)|divorced from)\b|"
    r"\b(?:i live (?:in|at)|i am based in|i'm based in|my home is in)\b|"
    r"\b\d{1,6}\s+\w+(?:\s\w+){0,3}\s+(?:street|st\.?|avenue|ave\.?|road|rd\.?|"
    r"boulevard|blvd\.?|lane|ln\.?|drive|dr\.?)\b|"
    r"\b(?:salary|annual income|bank account|credit card|make \$?\d)\b|"
    r"\bmy name is\b"
    r")",
    flags=re.IGNORECASE,
)

_MONTH = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?"
)
_NUMBER = (
    r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|billion)"
)
_PREFERENCE_VALUE_TERMS = (
    r"short|long|brief|detailed|concise|verbose|simple|complex|quick|thorough|dark|light"
)
_LEADING_REMEMBER = re.compile(
    r"^(?:please\s+)?(?:remember(?:\s+that)?|keep in mind(?:\s+that)?)\s+",
    flags=re.IGNORECASE,
)
_KIND_FRAMING = {
    "goal": re.compile(
        r"^(?:my goal is(?:\s+to)?|my goals are(?:\s+to)?|i aim to|i plan to|"
        r"we aim to|i want to (?:build|create|develop|ship|launch|learn|"
        r"become|achieve))\s+",
        flags=re.IGNORECASE,
    ),
    "project": re.compile(
        r"^(?:i am working on|i'm working on|we are working on|"
        r"we're working on|i am building|i'm building|we are building|"
        r"we're building|my project is)\s+",
        flags=re.IGNORECASE,
    ),
    "interaction_preference": re.compile(
        r"^(?:i (?:prefer|like|love|hate|dislike)|i do not like|i don't like|"
        r"my preference is|please always|please never|"
        r"when you (?:answer|respond)|prefer)\s+",
        flags=re.IGNORECASE,
    ),
    "preference": re.compile(
        r"^(?:i (?:prefer|like|love|hate|dislike)|i do not like|i don't like|"
        r"my preference is|please always|please never|prefer)\s+",
        flags=re.IGNORECASE,
    ),
    "editor_preference": re.compile(
        r"^(?:i (?:prefer|like)|my preference is|prefer)\s+",
        flags=re.IGNORECASE,
    ),
    "project_decision": re.compile(
        r"^(?:i decided(?:\s+to)?|we decided(?:\s+to)?|i chose|we chose|"
        r"we are going with|we're going with|i am going with|i'm going with|"
        r"we are using|we're using|i am using|i'm using)\s+",
        flags=re.IGNORECASE,
    ),
    "workflow": re.compile(
        r"^(?:i use|we use|my workflow is|our workflow is|my stack is|"
        r"our stack is)\s+",
        flags=re.IGNORECASE,
    ),
    "constraint": re.compile(
        r"^(?:i must|we must|i cannot|i can't|we cannot|we can't|"
        r"must not|must be|needs? to)\s+",
        flags=re.IGNORECASE,
    ),
}
_TEMPORAL_MODIFIER = re.compile(
    rf"\b(?:by|on|in|before|after|until|from)\s+(?:{_MONTH}|20\d{{2}}|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?)\b",
    flags=re.IGNORECASE,
)
_YEAR = re.compile(r"\b20\d{2}\b")
_QUANTITY = re.compile(
    rf"\b{_NUMBER}\s*(?:giga|mega|kilo)?(?:byte|bytes|gb|mb|kb)s?\b",
    flags=re.IGNORECASE,
)
_QUANTITY_BOUND = re.compile(
    rf"\b(?:under|over|at most|at least|no more than)\s+{_NUMBER}\b",
    flags=re.IGNORECASE,
)
_PREFERENCE_VALUE = re.compile(
    rf"\b(?:{_PREFERENCE_VALUE_TERMS})\b", flags=re.IGNORECASE
)
_CHOICE_BEFORE_FOR = re.compile(r"\b[\w.+-]+(?=\s+for\b)", flags=re.IGNORECASE)


def normalized_observation_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def classify_sensitivity(content: str, declared: Sensitivity = Sensitivity.NORMAL) -> Sensitivity:
    """Return the more conservative of the declared class and content hints."""

    detected = Sensitivity.NORMAL
    if _HIGHLY_SENSITIVE_HINT.search(content):
        detected = Sensitivity.HIGHLY_SENSITIVE
    elif _SENSITIVE_HINT.search(content):
        detected = Sensitivity.SENSITIVE
    if _SENSITIVITY_RANK[detected] >= _SENSITIVITY_RANK[declared]:
        return detected
    return declared


def archive_lineage_key(kind: str, content: str) -> str | None:
    """Stable subject-only identity for unkeyed archive statements of one kind.

    Returns None when the statement has no extractable subject. Callers must
    keep those records independent rather than collapsing them by kind.
    """

    normalized_kind = kind.strip().casefold()
    if normalized_kind not in UNKEYED_CONFLICT_KINDS:
        return None
    text = normalized_observation_text(content)
    text = _LEADING_REMEMBER.sub("", text).strip()
    framing = _KIND_FRAMING.get(normalized_kind)
    if framing is not None:
        text = framing.sub("", text).strip()
    text = _TEMPORAL_MODIFIER.sub(" ", text)
    text = _YEAR.sub(" ", text)
    text = _QUANTITY.sub(" ", text)
    text = _QUANTITY_BOUND.sub(" ", text)
    preference_value_removed = False
    if normalized_kind in {"preference", "interaction_preference", "editor_preference"}:
        text, removed = _PREFERENCE_VALUE.subn(" ", text)
        preference_value_removed = removed > 0
    if normalized_kind in {
        "preference",
        "interaction_preference",
        "editor_preference",
        "project_decision",
        "workflow",
    }:
        text = _CHOICE_BEFORE_FOR.sub("$choice", text)
    remainder = " ".join(text.split())
    tokens = [token for token in remainder.split() if len(token) > 1]
    # A one-token subject is useful only when the bounded preference-value
    # vocabulary exposed the subject (for example, ``dark mode`` -> ``mode``).
    # Unknown or value-free text still needs two subject tokens so this slot
    # cannot become a broad kind-only collapse.
    minimum_tokens = 1 if preference_value_removed else 2
    if len(tokens) < minimum_tokens:
        return None
    return f"{normalized_kind}:{' '.join(tokens[:12])}"[:256]


def effective_explicit_user_statement(
    claimed: bool,
    *,
    origin: ObservationOrigin,
    principal: ClientPrincipal | None,
) -> tuple[bool, str | None]:
    """Derive Core-authoritative explicitness from origin and witness grant.

    Clients may *claim* ``explicit_user_statement`` in a payload. Only an
    ATC-configured same-device principal with the closed
    ``witness:explicit_user_statement`` grant (or intentional ``admin``/``*``)
    may make that claim force applied current context on authenticated
    ongoing-client routes. Authentication and ``context:propose`` alone are
    insufficient.

    Local-admin and legacy-migration paths assign explicitness from Core.
    Core-controlled archive importers (no client principal) may also assign it
    for trusted parser output. Authenticated non-admin clients cannot re-label
    batch, provider, import, or Relay material as witnessed user evidence by
    smuggling origin, role, force, or ``source_type`` fields.
    """
    if not claimed:
        return False, None
    if origin in {
        ObservationOrigin.LOCAL_ADMIN,
        ObservationOrigin.LEGACY_MIGRATION,
    }:
        return True, None
    if origin == ObservationOrigin.ARCHIVE_IMPORT:
        # Principal is None only on the Core importer path. Authenticated
        # clients still need the closed witness grant (or admin/*).
        if principal is None or principal_may_attest_explicit_user_statement(principal):
            return True, None
        return False, "explicit claim reduced to tentative: principal lacks witness grant"
    if origin == ObservationOrigin.RELAY_QUEUE:
        return False, "remote relay proposals cannot attest direct user statements"
    if principal_may_attest_explicit_user_statement(principal):
        return True, None
    return False, "explicit claim reduced to tentative: principal lacks witness grant"


class AutomaticMemoryPolicy:
    """Classify an observation without performing any storage mutation."""

    def __init__(self, policy: MemoryPolicy | None = None) -> None:
        self.policy = policy or MemoryPolicy()

    def evaluate(
        self,
        candidate: CandidateInput,
        *,
        origin: ObservationOrigin,
        principal: ClientPrincipal | None = None,
    ) -> PolicyDecision:
        # Automatic observations never opt a record into Relay replication.
        # ``always_available`` remains a legacy/admin compatibility choice.
        availability = (
            Availability.CORE
            if candidate.availability == Availability.ALWAYS
            else candidate.availability
        )
        sensitivity = classify_sensitivity(candidate.content, candidate.sensitivity)
        is_correction = candidate.kind.casefold() == "correction"
        is_forget = candidate.kind.casefold() == "context_forget"
        claimed_explicit = bool(candidate.explicit_user_statement)
        effective_explicit, strip_reason = effective_explicit_user_statement(
            claimed_explicit,
            origin=origin,
            principal=principal,
        )

        def decide(
            disposition: ObservationDisposition,
            reason: str,
            decided_availability: Availability,
        ) -> PolicyDecision:
            return PolicyDecision(
                disposition,
                reason,
                decided_availability,
                sensitivity,
            )

        # Forget is an authenticated control request (privacy), not a fact that
        # becomes current context. It does not consume the explicit-statement
        # witness grant; ACL on the target still runs in storage. Validate the
        # structured intent before inspecting user-supplied reason text so
        # secret-like wording cannot prevent a privacy action.
        if is_forget:
            if claimed_explicit and candidate.supersedes is not None:
                return decide(
                    ObservationDisposition.APPLIED,
                    "explicit forget request applied as a reversible deletion",
                    Availability.LOCAL,
                )
            return decide(
                ObservationDisposition.IGNORED,
                "forget requests require explicit user intent and a record target",
                Availability.LOCAL,
            )
        if self.policy.mode != "automatic":
            return decide(
                ObservationDisposition.TENTATIVE,
                "automatic context maintenance is disabled",
                availability,
            )
        if contains_direct_secret(candidate):
            return decide(
                ObservationDisposition.IGNORED,
                "secret-like content is never promoted to current context",
                Availability.LOCAL,
            )
        if sensitivity == Sensitivity.HIGHLY_SENSITIVE:
            return decide(
                ObservationDisposition.IGNORED,
                "highly sensitive observations are excluded by automatic-v1",
                Availability.LOCAL,
            )
        if sensitivity == Sensitivity.SENSITIVE:
            if self.policy.sensitive_mode == "ignore":
                return decide(
                    ObservationDisposition.IGNORED,
                    "sensitive observations are disabled by vault policy",
                    Availability.LOCAL,
                )
            availability = Availability.LOCAL

        if is_correction and candidate.supersedes is None:
            return decide(
                ObservationDisposition.TENTATIVE,
                "a correction without a target is retained as a tentative signal",
                availability,
            )
        if is_correction and effective_explicit:
            return decide(
                ObservationDisposition.APPLIED,
                "explicit user correction applied automatically",
                availability,
            )

        if (
            origin == ObservationOrigin.ARCHIVE_IMPORT
            and candidate.source_type != "provider_archive"
        ):
            return decide(
                ObservationDisposition.TENTATIVE,
                "generic imported text is retained as untrusted evidence",
                availability,
            )
        if not effective_explicit:
            return decide(
                ObservationDisposition.TENTATIVE,
                strip_reason
                or "inferred or provider-generated observations require corroboration",
                availability,
            )
        if candidate.confidence < 0.5:
            return decide(
                ObservationDisposition.TENTATIVE,
                "low-confidence explicit observation retained for corroboration",
                availability,
            )

        reason = {
            ObservationOrigin.ARCHIVE_IMPORT: "explicit user-authored archive observation applied",
            ObservationOrigin.RELAY_QUEUE: "explicit remote user observation applied by Core",
            ObservationOrigin.CONTEXT_ERROR: "explicit user correction applied by Core",
            ObservationOrigin.LOCAL_ADMIN: "local administrator observation applied",
            ObservationOrigin.LEGACY_MIGRATION: "legacy explicit observation applied by policy",
            ObservationOrigin.ONGOING_CLIENT: "explicit user observation applied automatically",
        }[origin]
        return decide(ObservationDisposition.APPLIED, reason, availability)
