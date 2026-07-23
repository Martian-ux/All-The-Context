"""Deterministic automatic policy for context observations.

The policy never interprets imported text as instructions. Callers supply a
server-derived origin and the candidate's structured provenance; only Core
turns the resulting decision into current context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .models import (
    Availability,
    CandidateInput,
    ObservationDisposition,
    Sensitivity,
)

AUTOMATIC_POLICY_VERSION = "automatic-v1"

_SECRET_HINT = re.compile(
    r"(?:api[_ -]?key|password|passphrase|private[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|client[_ -]?secret|secret)\b\s*(?::|=|\bis\b|\bwas\b)",
    flags=re.IGNORECASE,
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


def normalized_observation_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _contains_secret_like_material(candidate: CandidateInput) -> bool:
    def structured_contains(value: object) -> bool:
        if isinstance(value, dict):
            return any(
                _SECRET_HINT.search(f"{key}:") is not None
                or structured_contains(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(structured_contains(item) for item in value)
        return isinstance(value, str) and _SECRET_HINT.search(value) is not None

    return (
        _SECRET_HINT.search(candidate.content) is not None
        or _SECRET_HINT.search(candidate.evidence or "") is not None
        or structured_contains(candidate.structured_value)
    )


class AutomaticMemoryPolicy:
    """Classify an observation without performing any storage mutation."""

    def __init__(self, policy: MemoryPolicy | None = None) -> None:
        self.policy = policy or MemoryPolicy()

    def evaluate(
        self,
        candidate: CandidateInput,
        *,
        origin: ObservationOrigin,
    ) -> PolicyDecision:
        # Automatic observations never opt a record into Relay replication.
        # ``always_available`` remains a legacy/admin compatibility choice.
        availability = (
            Availability.CORE
            if candidate.availability == Availability.ALWAYS
            else candidate.availability
        )
        is_correction = candidate.kind.casefold() == "correction"
        is_forget = candidate.kind.casefold() == "context_forget"
        # Forget is an authenticated control request, not retained context.
        # Validate its structured intent before inspecting the user-supplied
        # reason so secret-like wording cannot prevent a privacy action.
        if is_forget:
            if candidate.explicit_user_statement and candidate.supersedes is not None:
                return PolicyDecision(
                    ObservationDisposition.APPLIED,
                    "explicit forget request applied as a reversible deletion",
                    Availability.LOCAL,
                )
            return PolicyDecision(
                ObservationDisposition.IGNORED,
                "forget requests require explicit user intent and a record target",
                Availability.LOCAL,
            )
        if self.policy.mode != "automatic":
            return PolicyDecision(
                ObservationDisposition.TENTATIVE,
                "automatic context maintenance is disabled",
                availability,
            )
        if _contains_secret_like_material(candidate):
            return PolicyDecision(
                ObservationDisposition.IGNORED,
                "secret-like content is never promoted to current context",
                Availability.LOCAL,
            )
        if candidate.sensitivity == Sensitivity.HIGHLY_SENSITIVE:
            return PolicyDecision(
                ObservationDisposition.IGNORED,
                "highly sensitive observations are excluded by automatic-v1",
                Availability.LOCAL,
            )
        if candidate.sensitivity == Sensitivity.SENSITIVE:
            if self.policy.sensitive_mode == "ignore":
                return PolicyDecision(
                    ObservationDisposition.IGNORED,
                    "sensitive observations are disabled by vault policy",
                    Availability.LOCAL,
                )
            availability = Availability.LOCAL

        if is_correction and candidate.supersedes is None:
            return PolicyDecision(
                ObservationDisposition.TENTATIVE,
                "a correction without a target is retained as a tentative signal",
                availability,
            )
        if is_correction and candidate.explicit_user_statement:
            return PolicyDecision(
                ObservationDisposition.APPLIED,
                "explicit user correction applied automatically",
                availability,
            )

        if (
            origin == ObservationOrigin.ARCHIVE_IMPORT
            and candidate.source_type != "provider_archive"
        ):
            return PolicyDecision(
                ObservationDisposition.TENTATIVE,
                "generic imported text is retained as untrusted evidence",
                availability,
            )
        if not candidate.explicit_user_statement:
            return PolicyDecision(
                ObservationDisposition.TENTATIVE,
                "inferred or provider-generated observations require corroboration",
                availability,
            )
        if candidate.confidence < 0.5:
            return PolicyDecision(
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
        return PolicyDecision(ObservationDisposition.APPLIED, reason, availability)
