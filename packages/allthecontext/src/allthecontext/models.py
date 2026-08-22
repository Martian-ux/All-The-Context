"""Versioned public schemas shared by Core transports."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

MAX_CONTEXT_CHARS = 64_000
MAX_EVIDENCE_CHARS = 16_000
MAX_STRUCTURED_VALUE_BYTES = 64 * 1024
MAX_RECORD_LIST_ITEM_CHARS = 200
MAX_SLOT_KEY_CHARS = 256
MAX_CLOSED_COVERAGE_COUNT = 2_147_483_647
CLOSED_COVERAGE_KEYS = (
    "recognized",
    "excluded",
    "skipped",
    "unavailable",
    "duplicate",
    "failed",
    "unparsed",
)
CLOSED_COVERAGE_INCOMPLETE_KEYS = frozenset({"unavailable", "duplicate", "failed", "unparsed"})

ClosedCoverageCount = Annotated[
    StrictInt,
    Field(ge=0, le=MAX_CLOSED_COVERAGE_COUNT),
]
MAX_TRUTH_CONFLICT_GROUPS = 64
MAX_TRUTH_SUPERSEDED_BY = 64
MAX_TRUTH_EVIDENCE = 512

# These bounds mirror the production retrieval and bootstrap contracts. The
# candidate count may represent the complete bounded eligible pool rather than
# only the 100-record retrieval page; the selected pack remains capped at 32
# items and the public bootstrap budget at 100,000 characters.
MAX_CONTEXT_PACK_CANDIDATE_COUNT = 50_000
MAX_CONTEXT_PACK_SELECTED_COUNT = 32
MAX_CONTEXT_PACK_OMITTED_COUNT = MAX_CONTEXT_PACK_CANDIDATE_COUNT
MAX_CONTEXT_PACK_BUDGET_CHARS = 100_000
MAX_CONTEXT_PACK_USED_CHARS = MAX_CONTEXT_PACK_BUDGET_CHARS
MAX_CONTEXT_PACK_PROVENANCE_COUNT = MAX_CONTEXT_PACK_SELECTED_COUNT
MAX_CONTEXT_PACK_SUPPRESSED_COUNT = MAX_CONTEXT_PACK_CANDIDATE_COUNT

ContextPackTruncationReason = Literal[
    "candidate_pool",
    "budget",
    "record_limit",
    "edge_filter",
    "edge_envelope",
]
CONTEXT_PACK_TRUNCATION_REASON_ALLOWLIST = frozenset(
    {
        "candidate_pool",
        "budget",
        "record_limit",
        "edge_filter",
        "edge_envelope",
    }
)
EDGE_CONTEXT_PACK_TRUNCATION_REASONS = frozenset({"edge_filter", "edge_envelope"})
MAX_CONTEXT_PACK_TRUNCATION_REASONS = len(CONTEXT_PACK_TRUNCATION_REASON_ALLOWLIST)

RecordListItem = Annotated[
    str,
    Field(min_length=1, max_length=MAX_RECORD_LIST_ITEM_CHARS),
]


def _bounded_structured_value(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("structured_value must contain only finite JSON values") from exc
    if len(encoded) > MAX_STRUCTURED_VALUE_BYTES:
        raise ValueError(
            f"structured_value must be {MAX_STRUCTURED_VALUE_BYTES} UTF-8 bytes or smaller"
        )
    return value


def _normalized_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Availability(StrEnum):
    ALWAYS = "always_available"
    CORE = "core_available"
    LOCAL = "local_only"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    HIGHLY_SENSITIVE = "highly_sensitive"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ObservationDisposition(StrEnum):
    STAGED = "staged"
    APPLIED = "applied"
    REINFORCED = "reinforced"
    TENTATIVE = "tentative"
    IGNORED = "ignored"


class MemoryTruthStatus(StrEnum):
    """The canonical state of a record or detached observation."""

    CURRENT = "current"
    TENTATIVE = "tentative"
    SUPERSEDED = "superseded"
    CONFLICTED = "conflicted"
    DELETED = "deleted"


class TruthConflictState(StrEnum):
    """Conflict state is separate from record status so resolved history is visible."""

    NONE = "none"
    ACTIVE = "active"
    RESOLVED = "resolved"


class IngestionMode(StrEnum):
    BOOTSTRAP = "model_assisted_bootstrap"
    ARCHIVE = "archive_import"
    ONGOING = "ongoing"
    ERROR = "context_error"


class CandidateInput(StrictModel):
    kind: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)
    structured_value: dict[str, Any] | None = None
    entity_key: str | None = Field(default=None, min_length=1, max_length=MAX_SLOT_KEY_CHARS)
    attribute_key: str | None = Field(default=None, min_length=1, max_length=MAX_SLOT_KEY_CHARS)
    scopes: list[RecordListItem] = Field(default_factory=list, max_length=64)
    tags: list[RecordListItem] = Field(default_factory=list, max_length=128)
    source_id: str | None = Field(default=None, max_length=200)
    source_reference: str | None = Field(default=None, max_length=2_000)
    source_service: str | None = Field(default=None, max_length=256)
    source_type: str | None = Field(default=None, max_length=128)
    evidence: str | None = Field(default=None, max_length=MAX_EVIDENCE_CHARS)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sensitivity: Sensitivity = Sensitivity.NORMAL
    availability: Availability = Availability.CORE
    allowed_clients: list[RecordListItem] = Field(default_factory=list, max_length=256)
    denied_clients: list[RecordListItem] = Field(default_factory=list, max_length=256)
    observed_at: str | None = Field(default=None, max_length=100)
    valid_from: str | None = Field(default=None, max_length=100)
    expires_at: str | None = Field(default=None, max_length=100)
    supersedes: str | None = Field(default=None, max_length=200)
    explicit_user_statement: bool = False
    idempotency_key: str | None = Field(default=None, max_length=256)
    schema_version: int = Field(default=1, ge=1)

    @field_validator("kind", "scopes", "tags", "allowed_clients", "denied_clients")
    @classmethod
    def reject_control_characters(cls, value: Any) -> Any:
        values = value if isinstance(value, list) else [value]
        if any(any(ord(char) < 32 for char in item) for item in values):
            raise ValueError("control characters are not allowed")
        return value

    @field_validator("kind")
    @classmethod
    def normalize_kind(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("kind must contain non-whitespace text")
        return normalized

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must contain non-whitespace text")
        return value

    @field_validator("structured_value")
    @classmethod
    def bound_structured_value(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_structured_value(value)

    @field_validator("observed_at", "valid_from", "expires_at")
    @classmethod
    def normalize_timestamp(cls, value: str | None) -> str | None:
        return _normalized_timestamp(value)

    @model_validator(mode="after")
    def validate_time_window(self) -> Self:
        if (
            self.valid_from is not None
            and self.expires_at is not None
            and self.expires_at <= self.valid_from
        ):
            raise ValueError("expires_at must be later than valid_from")
        if (self.entity_key is None) != (self.attribute_key is None):
            raise ValueError("entity_key and attribute_key must be supplied together")
        return self


class BeginIngestionRequest(StrictModel):
    mode: IngestionMode
    accessible_sources: list[str] = Field(default_factory=list, max_length=512)
    unavailable_sources: list[str] = Field(default_factory=list, max_length=512)
    client_id: str | None = None
    notes: str | None = Field(default=None, max_length=8_000)
    idempotency_key: str | None = Field(default=None, max_length=256)


class SubmitBatchRequest(StrictModel):
    session_id: str
    idempotency_key: str = Field(min_length=1, max_length=256)
    candidates: list[CandidateInput] = Field(min_length=1, max_length=200)


class CoverageReport(StrictModel):
    available: list[str] = Field(default_factory=list, max_length=512)
    unavailable: list[str] = Field(default_factory=list, max_length=512)
    limitations: list[str] = Field(default_factory=list, max_length=512)
    warnings: list[str] = Field(default_factory=list, max_length=512)
    closed_coverage: dict[StrictStr, ClosedCoverageCount] = Field(
        default_factory=lambda: {key: 0 for key in CLOSED_COVERAGE_KEYS},
        max_length=16,
    )
    complete: StrictBool = True

    @field_validator("closed_coverage")
    @classmethod
    def validate_closed_coverage_keys(
        cls,
        value: dict[str, int],
    ) -> dict[str, int]:
        unknown = sorted(set(value).difference(CLOSED_COVERAGE_KEYS))
        if unknown:
            raise ValueError("closed_coverage contains unknown reason(s): " + ", ".join(unknown))
        return {key: value.get(key, 0) for key in CLOSED_COVERAGE_KEYS}

    @model_validator(mode="after")
    def validate_completion_consistency(self) -> Self:
        if self.complete and any(
            self.closed_coverage.get(key, 0) > 0 for key in CLOSED_COVERAGE_INCOMPLETE_KEYS
        ):
            raise ValueError(
                "complete cannot be true when closed_coverage contains incomplete items"
            )
        return self


class FinishIngestionRequest(StrictModel):
    session_id: str
    coverage: CoverageReport = Field(alias="coverage_report")


class ApprovalRequest(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=MAX_CONTEXT_CHARS)
    kind: str | None = Field(default=None, min_length=1, max_length=128)
    structured_value: dict[str, Any] | None = None
    entity_key: str | None = Field(default=None, min_length=1, max_length=MAX_SLOT_KEY_CHARS)
    attribute_key: str | None = Field(default=None, min_length=1, max_length=MAX_SLOT_KEY_CHARS)
    source_reference: str | None = Field(default=None, min_length=1, max_length=2_000)
    availability: Availability | None = None
    sensitivity: Sensitivity | None = None
    allowed_clients: list[RecordListItem] | None = Field(default=None, max_length=256)
    denied_clients: list[RecordListItem] | None = Field(default=None, max_length=256)
    reason: str | None = Field(default=None, max_length=2_000)
    explicit_sensitive_replication: bool = False

    @field_validator("kind")
    @classmethod
    def normalize_kind(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("kind must contain non-whitespace text")
        return normalized

    @field_validator("structured_value")
    @classmethod
    def bound_structured_value(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_structured_value(value)

    @model_validator(mode="after")
    def validate_slot(self) -> Self:
        if (self.entity_key is None) != (self.attribute_key is None):
            raise ValueError("entity_key and attribute_key must be supplied together")
        return self


class RejectRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=2_000)


class CorrectionRequest(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)
    reason: str = Field(min_length=1, max_length=2_000)
    structured_value: dict[str, Any] | None = None
    supersedes: str | None = Field(default=None, max_length=200)
    entity_key: str | None = Field(default=None, min_length=1, max_length=MAX_SLOT_KEY_CHARS)
    attribute_key: str | None = Field(default=None, min_length=1, max_length=MAX_SLOT_KEY_CHARS)

    @field_validator("structured_value")
    @classmethod
    def bound_structured_value(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_structured_value(value)

    @model_validator(mode="after")
    def validate_slot(self) -> Self:
        if (self.entity_key is None) != (self.attribute_key is None):
            raise ValueError("entity_key and attribute_key must be supplied together")
        return self


class RestoreRequest(StrictModel):
    version: int | None = Field(default=None, ge=1)
    reason: str = Field(default="restored by user", min_length=1, max_length=2_000)


class AvailabilityRequest(StrictModel):
    availability: Availability
    explicit_sensitive_replication: bool = False


class PurgeRequest(StrictModel):
    target_type: Literal["record", "source"] = "record"
    target_id: str = Field(min_length=1, max_length=200)
    confirmation: str = Field(min_length=1, max_length=512)
    compact: bool = True


class SearchRequest(StrictModel):
    query: str = Field(default="", max_length=4_000)
    scopes: list[str] = Field(default_factory=list, max_length=64)
    kinds: list[str] = Field(default_factory=list, max_length=64)
    availability: list[Availability] = Field(default_factory=list, max_length=3)
    sensitivity: list[Sensitivity] = Field(default_factory=list, max_length=3)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_ids: list[str] = Field(default_factory=list, max_length=64)
    as_of: str | None = Field(default=None, max_length=100)
    current_project: str | None = Field(default=None, max_length=512)
    limit: StrictInt = Field(default=20, ge=1, le=100)
    offset: StrictInt = Field(default=0, ge=0, le=100_000)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: str | None) -> str | None:
        return _normalized_timestamp(value)


class SearchCursor(StrictModel):
    """The signed, request-bound payload carried by a context-search cursor."""

    version: Literal[1]
    offset: StrictInt = Field(ge=0, le=100_000)
    request_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class BootstrapRequest(StrictModel):
    query: str = Field(default="", max_length=4_000, alias="task_description")
    requested_scopes: list[str] = Field(default_factory=list, max_length=64)
    budget_chars: int = Field(default=12_000, ge=256, le=100_000, alias="character_budget")
    current_project: str | None = Field(default=None, max_length=512)


class ClientCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=list, max_length=128)
    auto_approve: bool = False


class SourceOut(StrictModel):
    id: str
    content_hash: str
    source_service: str
    source_type: str
    filename: str | None
    media_type: str
    byte_size: int
    created_at: str
    duplicate: bool = False
    import_status: Literal["processing", "complete", "failed", "cancelled"] = "complete"
    metadata: dict[str, Any] = Field(default_factory=dict)
    parser_warnings: list[str] = Field(default_factory=list, max_length=512)
    candidate_count: int = Field(default=0, ge=0)
    deleted_at: str | None = None
    deleted_reason: str | None = None


class CandidateOut(CandidateInput):
    id: str
    session_id: str | None
    approval_status: ApprovalStatus
    content_hash: str
    created_at: str
    reviewed_at: str | None = None
    review_reason: str | None = None
    disposition: ObservationDisposition = ObservationDisposition.STAGED
    record_id: str | None = None
    decision_reason: str | None = None
    decided_at: str | None = None
    observation_origin: str | None = None
    policy_version: str | None = None


class ObservationOut(CandidateInput):
    id: str
    session_id: str | None
    submitted_by_client_id: str | None = None
    content_hash: str
    created_at: str
    disposition: ObservationDisposition
    record_id: str | None = None
    decision_reason: str | None = None
    decided_at: str | None = None
    observation_origin: str | None = None
    policy_version: str | None = None


class SecretRefusalOut(StrictModel):
    """Content-free receipt for a direct payload stopped before the ledger."""

    id: str
    refused: Literal[True] = True
    disposition: Literal["ignored"] = "ignored"
    reason_code: Literal["direct_secret_like_content"] = "direct_secret_like_content"
    detector_version: str
    created_at: str
    replayed: bool = False
    user_action_required: Literal[True] = True


class ContextRecordOut(CandidateInput):
    id: str
    approval_status: Literal[ApprovalStatus.APPROVED] = ApprovalStatus.APPROVED
    version: int
    content_hash: str
    created_at: str
    updated_at: str
    deleted_at: str | None = None
    status: MemoryTruthStatus = MemoryTruthStatus.CURRENT
    observation_origin: str | None = None
    policy_version: str | None = None


class TruthSourceOut(StrictModel):
    """Content-free source identity and lifecycle metadata for a memory."""

    id: str
    content_hash: str
    source_service: str
    source_type: str
    filename: str | None = None
    media_type: str
    created_at: str
    import_status: Literal["processing", "complete", "failed", "cancelled"]
    deleted_at: str | None = None
    deleted_reason: str | None = None


class TruthEvidenceOut(StrictModel):
    """One durable observation link explaining a canonical record."""

    observation_id: str
    record_id: str
    relationship: str
    link_created_at: str
    disposition: ObservationDisposition
    decision_reason: str | None = None
    decided_at: str | None = None
    observation_origin: str | None = None
    policy_version: str | None = None
    content: str
    evidence: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    source_id: str | None = None
    source_reference: str | None = None
    source_service: str | None = None
    source_type: str | None = None
    effective_at: str | None = None
    observed_at: str | None = None
    recorded_at: str
    content_hash: str


class MemoryTruthRecordOut(StrictModel):
    """Canonical record plus the bounded, deterministic explanation for it."""

    record: ContextRecordOut
    status: MemoryTruthStatus
    status_reason: str
    conflict_state: TruthConflictState = TruthConflictState.NONE
    conflict_group_ids: list[str] = Field(
        default_factory=list, max_length=MAX_TRUTH_CONFLICT_GROUPS
    )
    superseded_by: list[str] = Field(default_factory=list, max_length=MAX_TRUTH_SUPERSEDED_BY)
    source: TruthSourceOut | None = None
    evidence: list[TruthEvidenceOut] = Field(default_factory=list, max_length=MAX_TRUTH_EVIDENCE)
    history_count: int = Field(ge=0)


class MemoryTruthObservationOut(StrictModel):
    """A non-current observation retained so policy outcomes are not hidden."""

    observation: ObservationOut
    status: MemoryTruthStatus
    status_reason: str


class TruthCoverageOut(StrictModel):
    """Content-free accounting for clients that need to report import truthfully."""

    source_count: int = Field(ge=0)
    deleted_source_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    observations_by_disposition: dict[str, int] = Field(default_factory=dict)
    record_count: int = Field(ge=0)
    records_by_status: dict[str, int] = Field(default_factory=dict)
    conflict_group_count: int = Field(ge=0)
    ingestion_session_count: int = Field(ge=0)
    incomplete_ingestion_session_count: int = Field(ge=0)
    sessions_with_unavailable_sources: int = Field(ge=0)


class MemoryTruthResponse(StrictModel):
    """Administrative truth view; memory text is present only in authorized items."""

    items: list[MemoryTruthRecordOut]
    tentative_observations: list[MemoryTruthObservationOut] = Field(default_factory=list)
    total: int = Field(ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: TruthCoverageOut


class SearchResponse(StrictModel):
    items: list[ContextRecordOut]
    total: int
    trace_id: str


class ContextPackMetadata(StrictModel):
    """Strict, content-free accounting for a bounded provider context pack.

    Truncation reasons are intentionally closed: Core may emit the first three
    values and Edge may append only ``edge_filter`` or ``edge_envelope``.
    Selection and provenance counts describe the items in the final forwarded
    pack. Duplicate/conflict suppression counts remain explicitly scoped to
    Core's selection and are bounded by the final omitted population because
    Edge does not receive Core's suppression-group identities.
    """

    pack_schema: Literal["atc.context-pack.v1"] = Field(
        default="atc.context-pack.v1", alias="schema"
    )
    candidate_count: StrictInt = Field(ge=0, le=MAX_CONTEXT_PACK_CANDIDATE_COUNT)
    selected_count: StrictInt = Field(ge=0, le=MAX_CONTEXT_PACK_SELECTED_COUNT)
    omitted_count: StrictInt = Field(ge=0, le=MAX_CONTEXT_PACK_OMITTED_COUNT)
    budget_chars: StrictInt = Field(ge=0, le=MAX_CONTEXT_PACK_BUDGET_CHARS)
    used_chars: StrictInt = Field(ge=0, le=MAX_CONTEXT_PACK_USED_CHARS)
    provenance_backed_count: StrictInt = Field(ge=0, le=MAX_CONTEXT_PACK_PROVENANCE_COUNT)
    candidate_pool_truncated: StrictBool = False
    truncated: StrictBool = False
    # Core emits up to three reasons; Edge may add filtering and envelope
    # trimming while preserving those upstream reasons. Every value is an
    # explicit allowlisted literal, and duplicates are rejected.
    truncation_reasons: Annotated[list[ContextPackTruncationReason], Strict()] = Field(
        default_factory=list, max_length=MAX_CONTEXT_PACK_TRUNCATION_REASONS
    )
    duplicate_suppressed_count: StrictInt = Field(
        default=0, ge=0, le=MAX_CONTEXT_PACK_SUPPRESSED_COUNT
    )
    conflict_suppressed_count: StrictInt = Field(
        default=0, ge=0, le=MAX_CONTEXT_PACK_SUPPRESSED_COUNT
    )
    selection_policy: Literal["deterministic_usefulness_v1"] = "deterministic_usefulness_v1"

    @field_validator("truncation_reasons")
    @classmethod
    def reject_duplicate_truncation_reasons(
        cls, value: list[ContextPackTruncationReason]
    ) -> list[ContextPackTruncationReason]:
        if len(value) != len(set(value)):
            raise ValueError("truncation_reasons must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_accounting_invariants(self) -> Self:
        if self.selected_count > self.candidate_count:
            raise ValueError("selected_count must not exceed candidate_count")
        if self.omitted_count != self.candidate_count - self.selected_count:
            raise ValueError("omitted_count must equal candidate_count - selected_count")
        if self.provenance_backed_count > self.selected_count:
            raise ValueError("provenance_backed_count must not exceed selected_count")
        if self.duplicate_suppressed_count > self.omitted_count:
            raise ValueError("duplicate_suppressed_count must not exceed omitted_count")
        if self.conflict_suppressed_count > self.omitted_count:
            raise ValueError("conflict_suppressed_count must not exceed omitted_count")
        return self


class BootstrapResponse(StrictModel):
    items: list[ContextRecordOut]
    context_mode: Literal["local_core"] = "local_core"
    omitted_scopes: list[str]
    audit_trace_id: str
    used_chars: int
    pack_metadata: ContextPackMetadata | None = None


class ContextErrorRequest(StrictModel):
    record_id: str | None = None
    description: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)
    suggested_correction: str | None = Field(
        default=None, min_length=1, max_length=MAX_CONTEXT_CHARS
    )
    evidence: str | None = Field(default=None, max_length=MAX_EVIDENCE_CHARS)
    idempotency_key: str | None = Field(default=None, max_length=256)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        legacy_content = normalized.pop("content", None)
        if legacy_content is not None and normalized.get("suggested_correction") is None:
            normalized["suggested_correction"] = legacy_content
        if normalized.get("description") is None:
            if legacy_content is not None:
                normalized["description"] = legacy_content
            elif normalized.get("suggested_correction") is not None:
                normalized["description"] = normalized["suggested_correction"]
        return normalized

    @field_validator("description", "suggested_correction")
    @classmethod
    def reject_blank_error_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("context error text must contain non-whitespace text")
        return value


class ForgetContextRequest(StrictModel):
    record_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)
