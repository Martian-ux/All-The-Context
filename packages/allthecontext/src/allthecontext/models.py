"""Versioned public schemas shared by Core transports."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CONTEXT_CHARS = 64_000
MAX_EVIDENCE_CHARS = 16_000


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


class IngestionMode(StrEnum):
    BOOTSTRAP = "model_assisted_bootstrap"
    ARCHIVE = "archive_import"
    ONGOING = "ongoing"
    ERROR = "context_error"


class CandidateInput(StrictModel):
    kind: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)
    structured_value: dict[str, Any] | None = None
    scopes: list[str] = Field(default_factory=list, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=128)
    source_id: str | None = None
    source_reference: str | None = Field(default=None, max_length=2_000)
    source_service: str | None = Field(default=None, max_length=128)
    source_type: str | None = Field(default=None, max_length=128)
    evidence: str | None = Field(default=None, max_length=MAX_EVIDENCE_CHARS)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sensitivity: Sensitivity = Sensitivity.NORMAL
    availability: Availability = Availability.CORE
    allowed_clients: list[str] = Field(default_factory=list, max_length=256)
    denied_clients: list[str] = Field(default_factory=list, max_length=256)
    valid_from: str | None = None
    expires_at: str | None = None
    supersedes: str | None = None
    explicit_user_statement: bool = False
    idempotency_key: str | None = Field(default=None, max_length=256)
    schema_version: int = Field(default=1, ge=1)

    @field_validator("kind", "scopes", "tags")
    @classmethod
    def reject_control_characters(cls, value: Any) -> Any:
        values = value if isinstance(value, list) else [value]
        if any(any(ord(char) < 32 for char in item) for item in values):
            raise ValueError("control characters are not allowed")
        return value


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
    complete: bool = True


class FinishIngestionRequest(StrictModel):
    session_id: str
    coverage: CoverageReport = Field(alias="coverage_report")


class ApprovalRequest(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=MAX_CONTEXT_CHARS)
    availability: Availability | None = None
    sensitivity: Sensitivity | None = None
    allowed_clients: list[str] | None = None
    denied_clients: list[str] | None = None
    reason: str | None = Field(default=None, max_length=2_000)
    explicit_sensitive_replication: bool = False


class RejectRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=2_000)


class CorrectionRequest(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)
    reason: str = Field(min_length=1, max_length=2_000)
    structured_value: dict[str, Any] | None = None
    supersedes: str | None = None


class AvailabilityRequest(StrictModel):
    availability: Availability
    explicit_sensitive_replication: bool = False


class SearchRequest(StrictModel):
    query: str = Field(default="", max_length=4_000)
    scopes: list[str] = Field(default_factory=list, max_length=64)
    kinds: list[str] = Field(default_factory=list, max_length=64)
    availability: list[Availability] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)
    cursor: str | None = None


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


class CandidateOut(CandidateInput):
    id: str
    session_id: str | None
    approval_status: ApprovalStatus
    content_hash: str
    created_at: str
    reviewed_at: str | None = None
    review_reason: str | None = None


class ContextRecordOut(CandidateInput):
    id: str
    approval_status: Literal[ApprovalStatus.APPROVED] = ApprovalStatus.APPROVED
    version: int
    content_hash: str
    created_at: str
    updated_at: str


class SearchResponse(StrictModel):
    items: list[ContextRecordOut]
    total: int
    trace_id: str


class BootstrapResponse(StrictModel):
    items: list[ContextRecordOut]
    context_mode: Literal["local_core"] = "local_core"
    omitted_scopes: list[str]
    audit_trace_id: str
    used_chars: int


class ContextErrorRequest(StrictModel):
    record_id: str | None = None
    content: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS, alias="suggested_correction")
    description: str | None = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    evidence: str | None = Field(default=None, max_length=MAX_EVIDENCE_CHARS)
    idempotency_key: str | None = Field(default=None, max_length=256)
