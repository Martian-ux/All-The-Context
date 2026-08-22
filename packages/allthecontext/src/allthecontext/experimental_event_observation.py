"""Isolated Wave 1 Packet C event-to-observation formation contracts.

This module is an experimental, pure boundary.  It contains no persistence,
source ledger, replay, or canonical-record authority.  Lineage values are
opaque references to those authorities, and source text is treated as inert
evidence data even when it resembles an instruction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

MAX_REFERENCE_CHARS = 128
MAX_CONTENT_CHARS = 16_384
MAX_DERIVATION_REFERENCES = 16
MAX_AUTHORIZATION_LABELS = 64

_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_LIKE_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]+|basic\s+[a-z0-9._~+/=-]+|"
    r"sk-[a-z0-9]{4,}|gh[pousr]_[a-z0-9]{4,}|AIza[a-z0-9_-]{8,}|"
    r"(?:api[_ -]?key|access[_ -]?token|password|credential|secret|token)\s*[:=])"
)


class ContractErrorCode(StrEnum):
    """Content-free formation contract failure vocabulary."""

    INVALID_FIELD = "invalid_field"
    INVALID_LINEAGE = "invalid_lineage"
    INVALID_TIMESTAMP = "invalid_timestamp"
    SECRET_LIKE_CONTENT = "secret_like_content"
    RETENTION_EXPIRED = "retention_expired"
    CONTENT_MODE_MISMATCH = "content_mode_mismatch"
    DERIVED_WITHOUT_LINEAGE = "derived_without_lineage"
    UNTRUSTED_DERIVATION = "untrusted_derivation"
    AUTHORIZATION_CONFLICT = "authorization_conflict"
    DUPLICATE_REFERENCE = "duplicate_reference"


class ContractViolation(ValueError):
    """A bounded error whose message never includes supplied content."""

    def __init__(self, code: ContractErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _bounded_text(value: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise ContractViolation(ContractErrorCode.INVALID_FIELD)
    if not allow_empty and not value.strip():
        raise ContractViolation(ContractErrorCode.INVALID_FIELD)
    if len(value) > MAX_REFERENCE_CHARS or _CONTROL_CHARACTER_RE.search(value):
        raise ContractViolation(ContractErrorCode.INVALID_FIELD)
    return value


def _reference(value: str) -> str:
    return _bounded_text(value)


def _labels(value: Iterable[str], *, maximum: int = MAX_AUTHORIZATION_LABELS) -> frozenset[str]:
    values = frozenset(_reference(item) for item in value)
    if len(values) > maximum:
        raise ContractViolation(ContractErrorCode.INVALID_FIELD)
    return values


def _optional_labels(value: Iterable[str] | None) -> frozenset[str] | None:
    return None if value is None else _labels(value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractViolation(ContractErrorCode.INVALID_TIMESTAMP)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SourceLineage:
    """Authoritative source identity; no source payload is copied here."""

    source_id: str
    generation: int = 1
    revision: str = "initial"

    def __post_init__(self) -> None:
        _reference(self.source_id)
        _reference(self.revision)
        if type(self.generation) is not int or self.generation < 1:
            raise ContractViolation(ContractErrorCode.INVALID_LINEAGE)


@dataclass(frozen=True, slots=True)
class EventLineage:
    """Authoritative event identity and source-ordered position."""

    event_id: str
    source_id: str
    generation: int = 1
    sequence: int = 1
    revision: str = "initial"

    def __post_init__(self) -> None:
        _reference(self.event_id)
        _reference(self.source_id)
        _reference(self.revision)
        if (
            type(self.generation) is not int
            or self.generation < 1
            or type(self.sequence) is not int
            or self.sequence < 1
        ):
            raise ContractViolation(ContractErrorCode.INVALID_LINEAGE)


@dataclass(frozen=True, slots=True)
class ItemLineage:
    """Authoritative source-item identity and source-owned revision."""

    item_id: str
    source_id: str
    revision: str = "initial"

    def __post_init__(self) -> None:
        _reference(self.item_id)
        _reference(self.source_id)
        _reference(self.revision)


class WitnessClass(StrEnum):
    """Who or what directly witnessed the event."""

    DIRECT_USER = "direct_user"
    AUTHORITATIVE_SOURCE = "authoritative_source"
    HOST_ARTIFACT = "host_artifact"
    UNTRUSTED_IMPORTED_TEXT = "untrusted_imported_text"
    SYSTEM_DERIVATION = "system_derivation"


class EvidenceClass(StrEnum):
    """The bounded epistemic role of the proposed observation."""

    DIRECT_ASSERTION = "direct_assertion"
    SOURCE_ITEM = "source_item"
    OBSERVED_ARTIFACT = "observed_artifact"
    DERIVED_RELATION = "derived_relation"


class ObservationDisposition(StrEnum):
    """Formation dispositions; Core still owns canonical policy decisions."""

    TENTATIVE = "tentative"
    DERIVED = "derived"


class RetentionClass(StrEnum):
    """Bounded retention semantics for a formed proposal."""

    SESSION = "session"
    SOURCE_LIFETIME = "source_lifetime"
    EXPLICIT_EXPIRY = "explicit_expiry"
    USER_CONTROLLED = "user_controlled"


class ExpiryState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Retention class and optional exclusive expiry boundary."""

    retention_class: RetentionClass
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _utc(self.expires_at))
        if self.retention_class is RetentionClass.EXPLICIT_EXPIRY and self.expires_at is None:
            raise ContractViolation(ContractErrorCode.INVALID_FIELD)

    def state_at(self, as_of: datetime) -> ExpiryState:
        current = _utc(as_of)
        if self.expires_at is not None and current >= self.expires_at:
            return ExpiryState.EXPIRED
        return ExpiryState.ACTIVE

    def is_expired(self, as_of: datetime) -> bool:
        return self.state_at(as_of) is ExpiryState.EXPIRED


@dataclass(frozen=True, slots=True)
class AuthorizationApplicability:
    """The narrowest declared principal/scope applicability of evidence."""

    allowed_principals: frozenset[str] | None = None
    allowed_scopes: frozenset[str] | None = None
    denied_principals: frozenset[str] = field(default_factory=frozenset)
    denied_scopes: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_principals", _optional_labels(self.allowed_principals))
        object.__setattr__(self, "allowed_scopes", _optional_labels(self.allowed_scopes))
        object.__setattr__(self, "denied_principals", _labels(self.denied_principals))
        object.__setattr__(self, "denied_scopes", _labels(self.denied_scopes))
        if (
            self.allowed_principals is not None
            and self.allowed_principals & self.denied_principals
        ) or (
            self.allowed_scopes is not None and self.allowed_scopes & self.denied_scopes
        ):
            raise ContractViolation(ContractErrorCode.AUTHORIZATION_CONFLICT)

    def applies_to(self, principal: str, *, required_scopes: Iterable[str] = ()) -> bool:
        principal_ref = _reference(principal)
        scopes = _labels(required_scopes)
        if principal_ref in self.denied_principals or self.denied_scopes & scopes:
            return False
        if self.allowed_principals is not None and principal_ref not in self.allowed_principals:
            return False
        return self.allowed_scopes is None or scopes <= self.allowed_scopes

    def narrowed_by(self, narrower: AuthorizationApplicability) -> AuthorizationApplicability:
        """Return an intersection; a caller can never widen source applicability."""

        return AuthorizationApplicability(
            allowed_principals=_intersection(self.allowed_principals, narrower.allowed_principals),
            allowed_scopes=_intersection(self.allowed_scopes, narrower.allowed_scopes),
            denied_principals=self.denied_principals | narrower.denied_principals,
            denied_scopes=self.denied_scopes | narrower.denied_scopes,
        )

    def no_broader_than(self, broader: AuthorizationApplicability) -> bool:
        """Check the monotonic narrowing invariant without exposing content."""

        return (
            _allowed_subset(self.allowed_principals, broader.allowed_principals)
            and _allowed_subset(self.allowed_scopes, broader.allowed_scopes)
            and broader.denied_principals <= self.denied_principals
            and broader.denied_scopes <= self.denied_scopes
        )


def _intersection(
    left: frozenset[str] | None,
    right: frozenset[str] | None,
) -> frozenset[str] | None:
    if left is None:
        return right
    if right is None:
        return left
    return left & right


def _allowed_subset(
    narrower: frozenset[str] | None,
    broader: frozenset[str] | None,
) -> bool:
    if broader is None:
        return True
    return narrower is not None and narrower <= broader


class PayloadKind(StrEnum):
    BOUNDED_INLINE = "bounded_inline"
    AUTHORITATIVE_SOURCE_REFERENCE = "authoritative_source_reference"


class ContentInterpretation(StrEnum):
    EVIDENCE_DATA = "evidence_data"
    INERT_UNTRUSTED_DATA = "inert_untrusted_data"


@dataclass(frozen=True, slots=True)
class EventObservationInput:
    """Immutable, bounded input envelope for one formation attempt."""

    source: SourceLineage
    event: EventLineage
    item: ItemLineage
    witness_class: WitnessClass
    evidence_class: EvidenceClass
    retention: RetentionPolicy
    authorization: AuthorizationApplicability
    observed_at: datetime
    content: str | None = None
    payload_kind: PayloadKind = PayloadKind.BOUNDED_INLINE
    content_interpretation: ContentInterpretation = ContentInterpretation.EVIDENCE_DATA
    disposition: ObservationDisposition = ObservationDisposition.TENTATIVE
    supersedes_observation_ref: str | None = None
    derivation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.source.source_id != self.event.source_id
            or self.source.source_id != self.item.source_id
            or self.source.generation != self.event.generation
        ):
            raise ContractViolation(ContractErrorCode.INVALID_LINEAGE)
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if self.content is not None:
            if type(self.content) is not str or not self.content.strip():
                raise ContractViolation(ContractErrorCode.INVALID_FIELD)
            if len(self.content) > MAX_CONTENT_CHARS or _CONTROL_CHARACTER_RE.search(self.content):
                raise ContractViolation(ContractErrorCode.INVALID_FIELD)
        if self.payload_kind is PayloadKind.BOUNDED_INLINE and self.content is None:
            raise ContractViolation(ContractErrorCode.CONTENT_MODE_MISMATCH)
        if (
            self.payload_kind is PayloadKind.AUTHORITATIVE_SOURCE_REFERENCE
            and self.content is not None
        ):
            raise ContractViolation(ContractErrorCode.CONTENT_MODE_MISMATCH)
        if self.supersedes_observation_ref is not None:
            _reference(self.supersedes_observation_ref)
        refs = tuple(_reference(item) for item in self.derivation_refs)
        if len(refs) != len(set(refs)):
            raise ContractViolation(ContractErrorCode.DUPLICATE_REFERENCE)
        if len(refs) > MAX_DERIVATION_REFERENCES:
            raise ContractViolation(ContractErrorCode.INVALID_FIELD)
        object.__setattr__(self, "derivation_refs", refs)
        if self.disposition is ObservationDisposition.DERIVED:
            if self.witness_class is WitnessClass.UNTRUSTED_IMPORTED_TEXT:
                return
            if self.evidence_class is not EvidenceClass.DERIVED_RELATION or not refs:
                raise ContractViolation(ContractErrorCode.DERIVED_WITHOUT_LINEAGE)


@dataclass(frozen=True, slots=True)
class ObservationProposal:
    """A disposable proposal that Core may later evaluate authoritatively."""

    source: SourceLineage
    event: EventLineage
    item: ItemLineage
    witness_class: WitnessClass
    evidence_class: EvidenceClass
    retention: RetentionPolicy
    authorization: AuthorizationApplicability
    observed_at: datetime
    disposition: ObservationDisposition
    content: str | None
    content_interpretation: ContentInterpretation
    supersedes_observation_ref: str | None
    derivation_refs: tuple[str, ...]

    @property
    def lineage_key(self) -> tuple[str, str, str]:
        """Stable source/event/item key; no observation ID is minted here."""

        return (self.source.source_id, self.event.event_id, self.item.item_id)


class FormationStatus(StrEnum):
    FORMED = "formed"
    REFUSED = "refused"


class FormationRefusalCode(StrEnum):
    SECRET_LIKE_CONTENT = "secret_like_content"
    RETENTION_EXPIRED = "retention_expired"


@dataclass(frozen=True, slots=True)
class FormationRefusal:
    """Content-free result for a formation attempt stopped before persistence."""

    status: FormationStatus
    reason_code: FormationRefusalCode
    per_run_artifact_ref: str
    detector_version: str = "packet-c-v0"

    def __post_init__(self) -> None:
        if self.status is not FormationStatus.REFUSED:
            raise ContractViolation(ContractErrorCode.INVALID_FIELD)
        _reference(self.per_run_artifact_ref)
        _reference(self.detector_version)


@dataclass(frozen=True, slots=True)
class FormationResult:
    """Exactly one of proposal or content-free refusal is present."""

    status: FormationStatus
    proposal: ObservationProposal | None = None
    refusal: FormationRefusal | None = None

    def __post_init__(self) -> None:
        if (self.proposal is None) == (self.refusal is None):
            raise ContractViolation(ContractErrorCode.INVALID_FIELD)
        if self.status is FormationStatus.FORMED and self.proposal is None:
            raise ContractViolation(ContractErrorCode.INVALID_FIELD)
        if self.status is FormationStatus.REFUSED and self.refusal is None:
            raise ContractViolation(ContractErrorCode.INVALID_FIELD)

    @property
    def accepted(self) -> bool:
        return self.status is FormationStatus.FORMED


def form_observation(
    formation_input: EventObservationInput,
    *,
    as_of: datetime | None = None,
    refusal_ref: str = "formation-refusal-1",
) -> FormationResult:
    """Form one inert proposal or refuse it without retaining secret-like data."""

    if (
        formation_input.content is not None
        and _SECRET_LIKE_RE.search(formation_input.content) is not None
    ):
        return FormationResult(
            status=FormationStatus.REFUSED,
            refusal=FormationRefusal(
                status=FormationStatus.REFUSED,
                reason_code=FormationRefusalCode.SECRET_LIKE_CONTENT,
                per_run_artifact_ref=refusal_ref,
            ),
        )
    check_time = formation_input.observed_at if as_of is None else _utc(as_of)
    if formation_input.retention.is_expired(check_time):
        return FormationResult(
            status=FormationStatus.REFUSED,
            refusal=FormationRefusal(
                status=FormationStatus.REFUSED,
                reason_code=FormationRefusalCode.RETENTION_EXPIRED,
                per_run_artifact_ref=refusal_ref,
            ),
        )
    disposition = formation_input.disposition
    if formation_input.witness_class is WitnessClass.UNTRUSTED_IMPORTED_TEXT:
        disposition = ObservationDisposition.TENTATIVE
    proposal = ObservationProposal(
        source=formation_input.source,
        event=formation_input.event,
        item=formation_input.item,
        witness_class=formation_input.witness_class,
        evidence_class=formation_input.evidence_class,
        retention=formation_input.retention,
        authorization=formation_input.authorization,
        observed_at=formation_input.observed_at,
        disposition=disposition,
        content=formation_input.content,
        content_interpretation=formation_input.content_interpretation,
        supersedes_observation_ref=formation_input.supersedes_observation_ref,
        derivation_refs=formation_input.derivation_refs,
    )
    return FormationResult(status=FormationStatus.FORMED, proposal=proposal)


def narrow_proposal_authorization(
    proposal: ObservationProposal,
    restriction: AuthorizationApplicability,
) -> ObservationProposal:
    """Apply a monotonic applicability restriction to a disposable proposal."""

    return replace(proposal, authorization=proposal.authorization.narrowed_by(restriction))


def is_secret_like_content(value: str) -> bool:
    """Expose only the boolean detector result; callers cannot retrieve matches."""

    if type(value) is not str:
        raise ContractViolation(ContractErrorCode.INVALID_FIELD)
    return _SECRET_LIKE_RE.search(value) is not None
