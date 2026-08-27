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
from hashlib import sha256
from types import MappingProxyType

from .models import (
    Availability,
    CandidateInput,
    ObservationDisposition,
    Sensitivity,
)
from .secret_boundary import contains_direct_secret, contains_secret_like_text
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
    CLIENT_CAPTURE = "client_capture"
    LIVE_USER_EVIDENCE = "live_user_evidence"
    ARCHIVE_IMPORT = "archive_import"
    RELAY_QUEUE = "relay_queue"
    CONTEXT_ERROR = "context_error"
    LOCAL_ADMIN = "local_admin"
    LEGACY_MIGRATION = "legacy_migration"
    REGISTERED_SOURCE = "registered_source"


REGISTERED_SOURCE_FACT_KIND = "registered_source_fact"
REGISTERED_SOURCE_PROVIDER = "local-git-workspace"
REGISTERED_SOURCE_TYPE = "registered_capture"
REGISTERED_SOURCE_FACT_SCHEMA = "registered-source-fact-v1"
REGISTERED_SOURCE_EXTRACTOR_ID = "local-git-workspace-structure"
REGISTERED_SOURCE_EXTRACTOR_VERSION = 1
REGISTERED_SOURCE_FACT_CLASSES = frozenset(
    {
        "python_source",
        "markdown_documentation",
        "shell_script",
        "powershell_script",
        "project_manifest",
        "generic_text_file",
    }
)
REGISTERED_SOURCE_FACT_SENTENCES = MappingProxyType(
    {
        "python_source": "This workspace item is Python source.",
        "markdown_documentation": "This workspace item is Markdown documentation.",
        "shell_script": "This workspace item is a shell script.",
        "powershell_script": "This workspace item is a PowerShell script.",
        "project_manifest": "This workspace item is a known project manifest.",
        "generic_text_file": "This workspace item is a generic text file.",
    }
)
REGISTERED_SOURCE_CODE_OWNED_SCOPES: tuple[str, ...] = ("workspace.structure",)
REGISTERED_SOURCE_REFERENCE_PREFIX = "registered-source-item-"
REGISTERED_SOURCE_REFERENCE_RE = re.compile(
    rf"^{re.escape(REGISTERED_SOURCE_REFERENCE_PREFIX)}[0-9a-f]{{64}}$"
)
REGISTERED_SOURCE_IDEMPOTENCY_RE = re.compile(r"^capture-event-[0-9a-f]{64}$")
REGISTERED_SOURCE_SCOPE_RE = re.compile(r"^[A-Za-z0-9._:@/+-]{1,128}$")
REGISTERED_SOURCE_MAX_SCOPES = 64


def registered_source_reference(source_id: str, provider_item_id: str) -> str:
    """Return the opaque projection reference for one capture item."""

    digest = sha256(
        f"registered-source-reference-v1\0{source_id}\0{provider_item_id}".encode()
    ).hexdigest()
    return REGISTERED_SOURCE_REFERENCE_PREFIX + digest


def registered_source_fact_evidence(fact_class: str, binding_hash: str) -> str:
    """Return the exact Core-owned evidence string for a structural fact."""

    return (
        "Core registered-source structural fact; "
        f"schema={REGISTERED_SOURCE_FACT_SCHEMA}; "
        f"fact_class={fact_class}; binding={binding_hash}"
    )


def _registered_source_scopes_are_safe(scopes: object) -> bool:
    return (
        isinstance(scopes, list)
        and len(scopes) <= REGISTERED_SOURCE_MAX_SCOPES
        and all(
            isinstance(scope, str) and REGISTERED_SOURCE_SCOPE_RE.fullmatch(scope) is not None
            for scope in scopes
        )
        and tuple(scopes) == REGISTERED_SOURCE_CODE_OWNED_SCOPES
    )


def is_registered_source_fact(candidate: CandidateInput) -> bool:
    """Return whether a Core-created registered-source projection is closed."""

    structured = candidate.structured_value
    if not (
        candidate.kind == REGISTERED_SOURCE_FACT_KIND
        and candidate.source_service == REGISTERED_SOURCE_PROVIDER
        and candidate.source_type == REGISTERED_SOURCE_TYPE
        and candidate.source_id is None
        and not candidate.explicit_user_statement
        and candidate.schema_version == REGISTERED_SOURCE_EXTRACTOR_VERSION
        and candidate.entity_key is None
        and candidate.attribute_key is None
        and candidate.valid_from is None
        and candidate.expires_at is None
        and candidate.supersedes is None
        and candidate.confidence == 1.0
        and candidate.sensitivity == Sensitivity.NORMAL
        and candidate.availability == Availability.CORE
        and not candidate.tags
        and not candidate.allowed_clients
        and not candidate.denied_clients
        and _registered_source_scopes_are_safe(candidate.scopes)
        and candidate.observed_at is not None
        and isinstance(candidate.source_reference, str)
        and REGISTERED_SOURCE_REFERENCE_RE.fullmatch(candidate.source_reference) is not None
        and isinstance(candidate.idempotency_key, str)
        and REGISTERED_SOURCE_IDEMPOTENCY_RE.fullmatch(candidate.idempotency_key) is not None
        and isinstance(structured, dict)
        and set(structured)
        == {"binding_hash", "extractor", "extractor_version", "fact_class", "schema"}
        and structured.get("schema") == REGISTERED_SOURCE_FACT_SCHEMA
        and structured.get("extractor") == REGISTERED_SOURCE_EXTRACTOR_ID
        and type(structured.get("extractor_version")) is int
        and structured.get("extractor_version") == REGISTERED_SOURCE_EXTRACTOR_VERSION
        and isinstance(structured.get("binding_hash"), str)
        and len(structured["binding_hash"]) == 64
        and all(character in "0123456789abcdef" for character in structured["binding_hash"])
        and structured.get("fact_class") in REGISTERED_SOURCE_FACT_CLASSES
    ):
        return False
    fact_class = str(structured["fact_class"])
    binding_hash = str(structured["binding_hash"])
    return candidate.content == REGISTERED_SOURCE_FACT_SENTENCES[
        fact_class
    ] and candidate.evidence == registered_source_fact_evidence(fact_class, binding_hash)


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

_HEALTH_CONDITION = (
    r"(?:hiv|aids|cancer|diabetes|asthma|depression|anxiety(?: disorder)?|"
    r"epilepsy|bipolar disorder|ptsd|autism|adhd|a (?:medical )?(?:condition|"
    r"illness|disease|disorder|disability))"
)
_PERSONAL_HEALTH_HINT = (
    rf"(?:"
    rf"\b(?:i (?:was|am) diagnosed with|i (?:have|had|live with|am living with))\s+"
    rf"{_HEALTH_CONDITION}\b|"
    rf"\b(?:i (?:take|use|need|am on)\s+(?:medication|a prescription|"
    rf"chemotherapy|an inhaler)|"
    rf"my (?:diagnosis|condition|medication|prescription|therapist|doctor)\b|"
    rf"i am pregnant\b"
    rf"))"
)
_SENSITIVE_HINT = re.compile(
    rf"(?:"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"\b(?:phone|mobile)\s+number\b|"
    r"\b(?:date of birth|dob|birthday)\b|"
    rf"{_PERSONAL_HEALTH_HINT}|"
    r"\b(?:my (?:wife|husband|spouse|girlfriend|boyfriend|partner|"
    r"significant other|fiancé|fiance|fiancée|ex|kids?|children|son|daughter)|"
    r"divorced from)\b|"
    r"\b(?:(?:i )(?:(?:currently|presently) )?(?:live|reside) (?:in|at)|"
    r"i(?: am|'m) based in|i am residing (?:in|at)|i(?: am|'m) located in|"
    r"my (?:home|residence) is (?:located )?in|my address is)\b|"
    r"\b\d{1,6}\s+\w+(?:\s\w+){0,3}\s+(?:street|st\.?|avenue|ave\.?|road|rd\.?|"
    r"boulevard|blvd\.?|lane|ln\.?|drive|dr\.?)\b|"
    r"\b(?:my (?:salary|annual income|bank account|mortgage|home loan|"
    r"student loan|car loan|debt|lender)|i (?:have|owe|pay|hold)\s+"
    r"(?:a|an|my)?\s*(?:mortgage|home loan|student loan|car loan|debt)|"
    r"i (?:make|earn) \$?\d)\b|"
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
_PREFERENCE_VALUE = re.compile(rf"\b(?:{_PREFERENCE_VALUE_TERMS})\b", flags=re.IGNORECASE)
_CHOICE_BEFORE_FOR = re.compile(r"\b[\w.+-]+(?=\s+for\b)", flags=re.IGNORECASE)

_LIVE_PREFERENCE = re.compile(
    r"^(?:(?:i|we)\s+(?:(?:now|currently|still)\s+)?"
    r"(?:prefer|like|love|hate|dislike)|my\s+preference\s+is|prefer)\s+"
    r"(?P<value>.+)$",
    flags=re.IGNORECASE,
)
_LIVE_NAME = re.compile(r"^my\s+name\s+is\s+(?P<value>.+)$", flags=re.IGNORECASE)
_LIVE_LOCATION = re.compile(
    r"^(?:i\s+(?:live|reside)\s+(?:in|at)|my\s+address\s+is)\s+(?P<value>.+)$",
    flags=re.IGNORECASE,
)
_LIVE_SSN = re.compile(
    r"^my\s+social\s+security(?:\s+number)?\s+is\s+(?P<value>.+)$|"
    r"^my\s+ssn\s+is\s+(?P<value_alt>.+)$",
    flags=re.IGNORECASE,
)
_LIVE_HEALTH = re.compile(
    r"^(?:i\s+(?:was|am)\s+diagnosed\s+with|"
    r"i\s+(?:have|had|live\s+with|am\s+living\s+with))\s+(?P<value>.+)$",
    flags=re.IGNORECASE,
)
_LIVE_PROJECT = re.compile(
    r"^(?:i\s+(?:am|['`]m)\s+working\s+on|"
    r"we\s+(?:are|['`]re)\s+working\s+on|"
    r"(?:my|our)\s+current\s+project\s+is)\s+(?P<value>.+)$",
    flags=re.IGNORECASE,
)
_LIVE_GOAL = re.compile(
    r"^(?:my\s+goal\s+is(?:\s+to)?|i\s+aim\s+to|i\s+plan\s+to)\s+"
    r"(?P<value>.+)$",
    flags=re.IGNORECASE,
)
_LIVE_WORKFLOW = re.compile(
    r"^(?:i|we)\s+use\s+(?P<value>.+)$|"
    r"^(?:my|our)\s+(?:workflow|stack)\s+is\s+(?P<value_alt>.+)$",
    flags=re.IGNORECASE,
)
_PREFERENCE_FORMAT_VALUES = frozenset({"tabs", "spaces"})
_PREFERENCE_STYLE_VALUES = frozenset(
    {"brief", "concise", "detailed", "verbose", "thorough", "quick", "simple", "complex"}
)
_PREFERENCE_THEME_VALUES = frozenset({"dark", "light"})


@dataclass(frozen=True, slots=True)
class LiveUserClaim:
    """A deterministic, Core-formed claim extracted from one user turn."""

    kind: str
    content: str
    value: str
    attribute_key: str
    structured_value: dict[str, str]


def _claim_value(value: str) -> str:
    return value.strip().rstrip(".!?").strip()


def _preference_attribute(value: str) -> str:
    normalized = normalized_observation_text(value)
    first = normalized.split(maxsplit=1)[0] if normalized else ""
    if first in _PREFERENCE_FORMAT_VALUES:
        return "response_format"
    if first in _PREFERENCE_STYLE_VALUES:
        return "response_style"
    if first in _PREFERENCE_THEME_VALUES:
        return "interface_theme"
    subject_match = re.search(r"\s+for\s+(.+)$", normalized, flags=re.IGNORECASE)
    if subject_match is not None:
        subject = re.sub(r"[^a-z0-9]+", "_", subject_match.group(1)).strip("_")
        if subject:
            return f"preference_{subject}"[:256]
    return "preference"


def extract_live_user_claim(content: str) -> LiveUserClaim | None:
    """Extract only high-confidence first-person durable claims.

    This is deliberately narrower than a general prompt parser.  It reuses the
    same normalized wording and sensitivity classifier as archive formation,
    while keeping assistant/tool/imported text outside the user-claim path.
    """

    normalized = " ".join(unicodedata.normalize("NFKC", content).split()).strip()
    if not normalized or contains_secret_like_text(normalized):
        return None

    match = _LIVE_PREFERENCE.fullmatch(normalized)
    if match is not None:
        value = _claim_value(match.group("value"))
        if value:
            return LiveUserClaim(
                kind="interaction_preference",
                content=normalized,
                value=value,
                attribute_key=_preference_attribute(value),
                structured_value={"claim_type": "interaction_preference", "value": value},
            )

    match = _LIVE_NAME.fullmatch(normalized)
    if match is not None:
        value = _claim_value(match.group("value"))
        if value:
            return LiveUserClaim(
                kind="name",
                content=normalized,
                value=value,
                attribute_key="name",
                structured_value={"claim_type": "name", "value": value},
            )

    match = _LIVE_LOCATION.fullmatch(normalized)
    if match is not None:
        value = _claim_value(match.group("value"))
        if value:
            return LiveUserClaim(
                kind="personal_context",
                content=normalized,
                value=value,
                attribute_key="location",
                structured_value={"claim_type": "location", "value": value},
            )

    match = _LIVE_SSN.fullmatch(normalized)
    if match is not None:
        value = _claim_value(match.group("value") or match.group("value_alt") or "")
        if value:
            return LiveUserClaim(
                kind="personal_context",
                content=normalized,
                value=value,
                attribute_key="ssn",
                structured_value={"claim_type": "ssn", "value": value},
            )

    match = _LIVE_HEALTH.fullmatch(normalized)
    if match is not None and re.search(
        _PERSONAL_HEALTH_HINT, normalized, flags=re.IGNORECASE
    ) is not None:
        value = _claim_value(match.group("value"))
        if value:
            return LiveUserClaim(
                kind="personal_context",
                content=normalized,
                value=value,
                attribute_key="health",
                structured_value={"claim_type": "health", "value": value},
            )

    for pattern, kind, attribute_key in (
        (_LIVE_PROJECT, "project", "current_project"),
        (_LIVE_GOAL, "goal", "current_goal"),
    ):
        match = pattern.fullmatch(normalized)
        if match is not None:
            value = _claim_value(match.group("value"))
            if value:
                return LiveUserClaim(
                    kind=kind,
                    content=normalized,
                    value=value,
                    attribute_key=attribute_key,
                    structured_value={"claim_type": kind, "value": value},
                )

    match = _LIVE_WORKFLOW.fullmatch(normalized)
    if match is not None:
        value = _claim_value(match.group("value") or match.group("value_alt") or "")
        if value:
            return LiveUserClaim(
                kind="workflow",
                content=normalized,
                value=value,
                attribute_key="tooling",
                structured_value={"claim_type": "workflow", "value": value},
            )
    return None


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
        if origin == ObservationOrigin.REGISTERED_SOURCE:
            if not is_registered_source_fact(candidate):
                return decide(
                    ObservationDisposition.IGNORED,
                    "registered source fact schema is invalid",
                    Availability.LOCAL,
                )
            return decide(
                ObservationDisposition.APPLIED,
                "registered source structural fact applied",
                Availability.CORE,
            )
        if contains_direct_secret(candidate):
            return decide(
                ObservationDisposition.IGNORED,
                "secret-like content is never promoted to current context",
                Availability.LOCAL,
            )
        if origin == ObservationOrigin.CLIENT_CAPTURE:
            # A captured turn is evidence for the formation worker, never an
            # implicit durable-memory command. Preserve the Core-derived
            # sensitivity and local boundary while requiring later formation /
            # reconciliation before canonical memory can change.
            return decide(
                ObservationDisposition.TENTATIVE,
                "captured client evidence requires formation before current context",
                Availability.LOCAL,
            )
        if origin == ObservationOrigin.LIVE_USER_EVIDENCE:
            # Core-derived claims from a direct user turn are durable evidence,
            # not explicit remember/correct/forget commands.  The extractor is
            # intentionally narrow and this origin is never assigned to model,
            # tool, imported, Relay, or caller-authored candidates.
            return decide(
                ObservationDisposition.APPLIED,
                "Core-formed live user evidence applied",
                Availability.LOCAL if sensitivity != Sensitivity.NORMAL else availability,
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
                strip_reason or "inferred or provider-generated observations require corroboration",
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
            ObservationOrigin.CLIENT_CAPTURE: "captured client evidence applied by Core",
            ObservationOrigin.LIVE_USER_EVIDENCE: "Core-formed live user evidence applied",
            ObservationOrigin.REGISTERED_SOURCE: "registered source structural fact applied",
        }[origin]
        return decide(ObservationDisposition.APPLIED, reason, availability)
