"""Deterministic automatic policy for context observations.

The policy never interprets imported text as instructions. Callers supply a
server-derived origin and the candidate's structured provenance; only Core
turns the resulting decision into current context.
"""

from __future__ import annotations

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


def normalized_observation_text(value: str) -> str:
    return " ".join(value.casefold().split())


def effective_explicit_user_statement(
    claimed: bool,
    *,
    origin: ObservationOrigin,
    principal: ClientPrincipal | None,
) -> tuple[bool, str | None]:
    """Derive Core-authoritative explicitness from origin and witness grant.

    Clients may *claim* explicit_user_statement in a payload. Only an
    ATC-configured same-device principal with the explicit-statement witness
    grant (or admin/*) may make that claim force applied current context.
    Authentication and ``context:propose`` alone are insufficient. Archive and
    local-admin paths assign explicitness from Core, not client self-escalation.
    """
    if not claimed:
        return False, None
    if origin in {
        ObservationOrigin.LOCAL_ADMIN,
        ObservationOrigin.LEGACY_MIGRATION,
        ObservationOrigin.ARCHIVE_IMPORT,
    }:
        return True, None
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
        is_correction = candidate.kind.casefold() == "correction"
        is_forget = candidate.kind.casefold() == "context_forget"
        claimed_explicit = bool(candidate.explicit_user_statement)
        effective_explicit, strip_reason = effective_explicit_user_statement(
            claimed_explicit,
            origin=origin,
            principal=principal,
        )
        # Forget is an authenticated control request (privacy), not a fact that
        # becomes current context. It does not consume the explicit-statement
        # witness grant; ACL on the target still runs in storage. Validate the
        # structured intent before inspecting user-supplied reason text so
        # secret-like wording cannot prevent a privacy action.
        if is_forget:
            if claimed_explicit and candidate.supersedes is not None:
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
        if contains_direct_secret(candidate):
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
        if is_correction and effective_explicit:
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
        if not effective_explicit:
            return PolicyDecision(
                ObservationDisposition.TENTATIVE,
                strip_reason or "inferred or provider-generated observations require corroboration",
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
