# ruff: noqa: E501
"""Content-free Continuous Capture contracts and the local capture ledger.

This module deliberately stops at a provider-neutral, foreground-only ledger.
Adapters are injected by tests or by ``capture_runtime`` when a valid
machine-local workspace authorization exists. This repository does not contain
a network implementation or a credential-bearing adapter.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from .activity import CoreActivityGate
from .ids import new_id, utc_now
from .secret_boundary import contains_secret_like_text
from .storage import (
    MAX_ACTIVITY_SNAPSHOT_ITEMS,
    ConflictError,
    CoreStore,
    NotFoundError,
    StorageError,
    _added_column,
    _migration_statements,
)

CaptureLifecycleState = Literal[
    "disabled",
    "enabled",
    "paused",
    "degraded",
    "revoked",
    "reconciling",
]
CaptureOperation = Literal["upsert", "delete"]
CaptureEventState = Literal["staged", "applied", "failed"]
CaptureRunState = Literal["running", "completed", "failed", "abandoned"]
CaptureCapabilityAvailability = Literal["complete", "partial", "unavailable"]
CaptureAcquisitionMode = Literal[
    "initial_snapshot",
    "incremental",
    "snapshot_and_incremental",
    "unavailable",
    "legacy",
]
CaptureCoverageState = Literal["complete", "partial", "unavailable"]
CaptureFreshnessState = Literal["fresh", "stale", "unknown", "unavailable"]
CaptureAuthorizationState = Literal[
    "authorized",
    "reauthorization_required",
    "unauthorized",
    "unknown",
]
CaptureConnectionState = Literal["connected", "disconnected", "unknown"]
CaptureHealthState = Literal["healthy", "degraded", "unavailable"]
CaptureRateLimitMode = Literal["none", "retry_after", "bounded_backoff", "unavailable"]
CaptureDeletionCoordination = Literal["coordinated", "unsupported"]
CapturePurgeCoordination = Literal["coordinated", "external_only", "unsupported"]
CaptureNetworkAccessState = Literal["allowed", "denied", "unknown"]

CAPTURE_CAPABILITY_MANIFEST_VERSION = "v0"

MAX_PROVIDER_CHARS = 128
MAX_ACCOUNT_LABEL_CHARS = 200
MAX_FINGERPRINT_CHARS = 256
MAX_SCOPE_COUNT = 64
MAX_SCOPE_CHARS = 128
MAX_CURSOR_CHARS = 1024
MAX_EVENT_ID_CHARS = 256
MAX_ORDER_KEY_CHARS = 256
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_PAYLOAD_DEPTH = 8
MAX_PAYLOAD_KEYS = 64
MAX_PAYLOAD_LIST_ITEMS = 128
MAX_PAYLOAD_STRING_CHARS = 2_000
MAX_CAPTURE_CONTENT_CHARS = 16_384
MAX_PAGE_EVENTS = 256
MAX_RUN_PAGES = 100
MAX_RUN_EVENTS = 10_000
MAX_ERROR_CHARS = 96
MAX_PENDING_EVENT_IDS_BYTES = MAX_PAGE_EVENTS * (MAX_EVENT_ID_CHARS + 3) + 2
LEASE_SECONDS = 60
MAX_CAPTURE_INTEGER = (1 << 63) - 1

CAPTURE_ERROR_CODES = frozenset(
    {
        "capture_adapter_unavailable",
        "capture_source_not_enabled",
        "capture_source_degraded",
        "capture_run_in_progress",
        "capture_lease_expired",
        "capture_capability_invalid",
        "capture_reauthorization_required",
        "capture_authorization_unavailable",
        "capture_disconnected",
        "capture_retryable_failure",
        "capture_retry_exhausted",
        "capture_page_malformed",
        "capture_invalid_cursor",
        "capture_page_limit_exceeded",
        "capture_event_limit_exceeded",
        "capture_event_out_of_order",
        "capture_event_gap",
        "capture_event_generation_mismatch",
        "capture_event_payload_conflict",
        "capture_payload_rejected",
        "capture_sink_failed",
        "capture_sink_receipt_invalid",
        "capture_lineage_conflict",
        "capture_local_only_required",
        "capture_contract_invalid",
        "capture_authorize_workspace_required",
        "capture_invalid_transition",
        "capture_failed",
    }
)

_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:@/+-]+$")
_SENSITIVE_KEY_RE = re.compile(r"(?i)(token|secret|password|credential|authorization|api[_-]?key)")


class CaptureError(StorageError):
    """A bounded, transport-safe capture failure."""

    def __init__(self, code: str) -> None:
        if code not in CAPTURE_ERROR_CODES:
            code = "capture_failed"
        self.code = code
        super().__init__(code)


class CaptureTransitionError(CaptureError):
    """Raised when a source lifecycle transition is not permitted."""

    def __init__(self) -> None:
        super().__init__("capture_invalid_transition")


class CaptureRetryableError(CaptureError):
    """Synthetic/provider-neutral signal for a bounded retryable fetch failure."""

    def __init__(self, retry_after_seconds: int | None = None) -> None:
        if retry_after_seconds is not None and (
            type(retry_after_seconds) is not int or not 0 <= retry_after_seconds <= 86_400
        ):
            raise ValueError("invalid capture retry-after value")
        self.retry_after_seconds = retry_after_seconds
        super().__init__("capture_retryable_failure")


def _secret_scan_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    return "".join(
        char for char in decomposed if unicodedata.category(char) not in {"Cf", "Mn", "Mc", "Me"}
    )


def _bounded_text(value: str, *, maximum: int, code: str = "capture_page_malformed") -> str:
    if not isinstance(value, str):
        raise CaptureError(code)
    text = value.strip()
    if not text or len(text) > maximum or _SAFE_TEXT_RE.fullmatch(text) is None:
        raise CaptureError(code)
    if contains_secret_like_text(text):
        raise CaptureError("capture_payload_rejected")
    return text


def _bounded_opaque_id(value: str, *, maximum: int) -> str:
    text = _bounded_text(value, maximum=maximum)
    if _SAFE_ID_RE.fullmatch(text) is None:
        raise CaptureError("capture_page_malformed")
    return text


def _bounded_cursor(value: str, *, maximum: int = MAX_CURSOR_CHARS) -> str:
    """Validate a cursor as opaque text without imposing a provider alphabet."""

    return _bounded_text(value, maximum=maximum, code="capture_invalid_cursor")


def _bounded_content(value: str) -> str:
    """Validate captured conversation content with the formation bound."""

    if not isinstance(value, str):
        raise CaptureError("capture_payload_rejected")
    if (
        not value.strip()
        or len(value) > MAX_CAPTURE_CONTENT_CHARS
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value) is not None
        or contains_secret_like_text(value)
    ):
        raise CaptureError("capture_payload_rejected")
    return value


def _normalize_payload(value: Any, *, depth: int = 0, key: str | None = None) -> Any:
    if depth > MAX_PAYLOAD_DEPTH:
        raise CaptureError("capture_payload_rejected")
    if key is not None:
        if len(key) > MAX_PAYLOAD_STRING_CHARS or _SENSITIVE_KEY_RE.search(_secret_scan_text(key)):
            raise CaptureError("capture_payload_rejected")
        _bounded_text(key, maximum=MAX_PAYLOAD_STRING_CHARS, code="capture_payload_rejected")
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        if not -MAX_CAPTURE_INTEGER <= value <= MAX_CAPTURE_INTEGER:
            raise CaptureError("capture_payload_rejected")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CaptureError("capture_payload_rejected")
        return value
    if isinstance(value, str):
        if key == "content":
            return _bounded_content(value)
        text = _bounded_text(
            value,
            maximum=MAX_PAYLOAD_STRING_CHARS,
            code="capture_payload_rejected",
        )
        return text
    if isinstance(value, Mapping):
        if len(value) > MAX_PAYLOAD_KEYS:
            raise CaptureError("capture_payload_rejected")
        if any(not isinstance(raw_key, str) for raw_key in value):
            raise CaptureError("capture_payload_rejected")
        result: dict[str, Any] = {}
        for raw_key, raw_value in sorted(value.items()):
            result[raw_key] = _normalize_payload(raw_value, depth=depth + 1, key=raw_key)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_PAYLOAD_LIST_ITEMS:
            raise CaptureError("capture_payload_rejected")
        return [_normalize_payload(item, depth=depth + 1) for item in value]
    raise CaptureError("capture_payload_rejected")


def _payload_json(payload: Mapping[str, Any]) -> tuple[str, str]:
    normalized = _normalize_payload(payload)
    if not isinstance(normalized, dict):
        raise CaptureError("capture_payload_rejected")
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise CaptureError("capture_payload_rejected")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CaptureSource:
    """Content-free source projection used by adapters and admin callers."""

    id: str
    provider: str
    account_label: str
    account_fingerprint: str | None
    requested_scopes: tuple[str, ...]
    local_only: bool
    local_only_acknowledged: bool
    lifecycle_state: CaptureLifecycleState
    retry_count: int
    next_retry_at: str | None
    last_error_code: str | None
    last_error_at: str | None
    lag_events: int
    lag_pages: int
    created_at: str
    updated_at: str
    last_run_at: str | None

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "id": self.id,
            "provider": self.provider,
            "account_label": self.account_label,
            "account_fingerprint": self.account_fingerprint,
            "requested_scopes": list(self.requested_scopes),
            "local_only": self.local_only,
            "local_only_acknowledged": self.local_only_acknowledged,
            "lifecycle_state": self.lifecycle_state,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "last_error_code": self.last_error_code,
            "last_error_at": self.last_error_at,
            "lag_events": self.lag_events,
            "lag_pages": self.lag_pages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
        }


@dataclass(frozen=True, slots=True)
class CaptureEvent:
    provider_event_id: str
    provider_item_id: str
    order_key: str
    operation: CaptureOperation = "upsert"
    payload: Mapping[str, Any] = field(default_factory=dict)
    generation: int = 0

    def __post_init__(self) -> None:
        _bounded_opaque_id(self.provider_event_id, maximum=MAX_EVENT_ID_CHARS)
        _bounded_opaque_id(self.provider_item_id, maximum=MAX_EVENT_ID_CHARS)
        _bounded_opaque_id(self.order_key, maximum=MAX_ORDER_KEY_CHARS)
        if (
            not isinstance(self.operation, str)
            or self.operation not in {"upsert", "delete"}
            or type(self.generation) is not int
            or not 0 <= self.generation <= MAX_CAPTURE_INTEGER
        ):
            raise CaptureError("capture_page_malformed")
        if not isinstance(self.payload, Mapping):
            raise CaptureError("capture_payload_rejected")
        if self.operation == "delete" and self.payload:
            # Delete events carry no provider content.  The source/item lineage
            # is supplied separately to the sink by the coordinator.
            raise CaptureError("capture_payload_rejected")

    def normalized(self) -> tuple[str, str]:
        return _payload_json(self.payload)


@dataclass(frozen=True, slots=True)
class CapturePage:
    generation: int
    events: tuple[CaptureEvent, ...] = field(default_factory=tuple)
    next_cursor: str | None = None
    page_order: int = 0
    done: bool = True
    coverage: CaptureCoverageState | None = None
    freshness: CaptureFreshnessState | None = None

    def __post_init__(self) -> None:
        if (
            type(self.generation) is not int
            or type(self.page_order) is not int
            or not 0 <= self.generation <= MAX_CAPTURE_INTEGER
            or not 0 <= self.page_order <= MAX_CAPTURE_INTEGER
        ):
            raise CaptureError("capture_page_malformed")
        if isinstance(self.events, list):
            object.__setattr__(self, "events", tuple(self.events))
        elif not isinstance(self.events, tuple):
            raise CaptureError("capture_page_malformed")
        if len(self.events) > MAX_PAGE_EVENTS:
            raise CaptureError("capture_event_limit_exceeded")
        if self.next_cursor is not None:
            _bounded_cursor(self.next_cursor)
        if not isinstance(self.done, bool):
            raise CaptureError("capture_page_malformed")
        if self.coverage is not None and (
            not isinstance(self.coverage, str)
            or self.coverage not in {"complete", "partial", "unavailable"}
        ):
            raise CaptureError("capture_page_malformed")
        if self.freshness is not None and (
            not isinstance(self.freshness, str)
            or self.freshness not in {"fresh", "stale", "unknown", "unavailable"}
        ):
            raise CaptureError("capture_page_malformed")
        for event in self.events:
            if not isinstance(event, CaptureEvent):
                raise CaptureError("capture_page_malformed")
        if not self.done and self.next_cursor is None:
            raise CaptureError("capture_invalid_cursor")


@dataclass(frozen=True, slots=True)
class CaptureApplicationReceipt:
    receipt: str
    canonical_record_id: str


@dataclass(frozen=True, slots=True, init=False)
class CaptureRunHandle:
    """Opaque capability for mutating one still-owned capture run."""

    run_id: str
    source_id: str
    lease_token: str = field(repr=False)

    @classmethod
    def _mint(cls, run_id: str, source_id: str, lease_token: str) -> CaptureRunHandle:
        handle = object.__new__(cls)
        object.__setattr__(handle, "run_id", run_id)
        object.__setattr__(handle, "source_id", source_id)
        object.__setattr__(handle, "lease_token", lease_token)
        return handle

    def __repr__(self) -> str:
        return f"CaptureRunHandle(run_id={self.run_id!r}, source_id={self.source_id!r})"


class CaptureProviderAdapter(Protocol):
    """Provider-neutral page source; implementations must be injected.

    ``capability_manifest`` is intentionally an attribute rather than a new
    connector/event path.  Older fetch-only adapters remain usable through
    the coordinator's explicit legacy compatibility default.
    """

    @property
    def capability_manifest(self) -> CaptureCapabilityManifest: ...

    def fetch_page(
        self,
        source: CaptureSource,
        cursor: str | None,
        page_order: int,
    ) -> CapturePage: ...


class CaptureApplicationSink(Protocol):
    """Idempotent canonical application boundary.

    The source and provider item are always passed separately so a provider
    delete cannot target another source's lineage.  Implementations must keep
    local corrections authoritative and must treat ``idempotency_key`` as a
    durable no-op key.
    """

    def apply(
        self,
        event: CaptureEvent,
        *,
        source_id: str,
        event_id: str,
        run_handle: CaptureRunHandle,
        canonical_record_id: str,
        idempotency_key: str,
    ) -> str | CaptureApplicationReceipt: ...


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    base_seconds: int = 1
    max_seconds: int = 3_600

    def __post_init__(self) -> None:
        if (
            type(self.base_seconds) is not int
            or type(self.max_seconds) is not int
            or self.base_seconds < 0
            or self.max_seconds < self.base_seconds
        ):
            raise ValueError("invalid capture backoff policy")

    def delay_seconds(self, attempt: int) -> int:
        bounded_attempt = max(1, min(attempt, 31))
        exponential_delay = self.base_seconds * (1 << (bounded_attempt - 1))
        return min(self.max_seconds, exponential_delay)


@dataclass(frozen=True, slots=True)
class CaptureRateLimitPolicy:
    """Bounded declaration of how an adapter handles provider rate limits."""

    mode: CaptureRateLimitMode = "none"
    max_delay_seconds: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in {
            "none",
            "retry_after",
            "bounded_backoff",
            "unavailable",
        }:
            raise ValueError("invalid capture rate-limit mode")
        if type(self.max_delay_seconds) is not int or not 0 <= self.max_delay_seconds <= 86_400:
            raise ValueError("invalid capture rate-limit delay")
        if self.mode in {"none", "unavailable"} and self.max_delay_seconds != 0:
            raise ValueError("invalid capture rate-limit declaration")
        if self.mode in {"retry_after", "bounded_backoff"} and self.max_delay_seconds <= 0:
            raise ValueError("invalid capture rate-limit declaration")

    def model_dump(self) -> dict[str, Any]:
        return {"mode": self.mode, "max_delay_seconds": self.max_delay_seconds}


@dataclass(frozen=True, slots=True)
class CaptureRetryPolicy:
    """Bounded retry declaration; scheduling remains Core-owned."""

    retryable_failures: bool = True
    max_attempts: int = 3
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)
    rate_limit: CaptureRateLimitPolicy = field(default_factory=CaptureRateLimitPolicy)

    def __post_init__(self) -> None:
        if type(self.retryable_failures) is not bool:
            raise ValueError("invalid capture retry declaration")
        if (
            type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= 16
            or (not self.retryable_failures and self.max_attempts != 1)
            or not isinstance(self.backoff, BackoffPolicy)
            or not isinstance(self.rate_limit, CaptureRateLimitPolicy)
        ):
            raise ValueError("invalid capture retry declaration")

    def model_dump(self) -> dict[str, Any]:
        return {
            "retryable_failures": self.retryable_failures,
            "max_attempts": self.max_attempts,
            "backoff": {
                "base_seconds": self.backoff.base_seconds,
                "max_seconds": self.backoff.max_seconds,
            },
            "rate_limit": self.rate_limit.model_dump(),
        }


@dataclass(frozen=True, slots=True)
class CaptureCapabilityConformance:
    """Bounded result of checking one immutable capability declaration."""

    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise CaptureError("capture_capability_invalid")
        for values in (self.errors, self.warnings):
            if not isinstance(values, tuple) or len(values) > 16:
                raise CaptureError("capture_capability_invalid")
            if any(
                not isinstance(value, str)
                or not value
                or len(value) > MAX_ERROR_CHARS
                or _SAFE_ID_RE.fullmatch(value) is None
                for value in values
            ):
                raise CaptureError("capture_capability_invalid")

    def model_dump(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class CaptureCapabilityManifest:
    """Experimental v0 provider contract; it contains no credential values."""

    provider: str
    version: str = CAPTURE_CAPABILITY_MANIFEST_VERSION
    availability: CaptureCapabilityAvailability = "complete"
    acquisition_mode: CaptureAcquisitionMode = "snapshot_and_incremental"
    initial_snapshot: bool = True
    incremental: bool = True
    cursor_support: bool = True
    coverage: CaptureCoverageState = "complete"
    coverage_reason: str | None = None
    freshness: CaptureFreshnessState = "fresh"
    authorization: CaptureAuthorizationState = "authorized"
    credential_ref: str | None = field(default=None, repr=False)
    retry_policy: CaptureRetryPolicy = field(default_factory=CaptureRetryPolicy)
    connection: CaptureConnectionState = "connected"
    disconnect_supported: bool = False
    source_deletion: CaptureDeletionCoordination = "unsupported"
    purge_coordination: CapturePurgeCoordination = "unsupported"
    network_access: CaptureNetworkAccessState | bool = "denied"
    data_egress: tuple[str, ...] | None = field(default_factory=tuple)
    health: CaptureHealthState = "healthy"
    health_diagnostics: tuple[str, ...] = field(default_factory=tuple)
    legacy_compatibility: bool = False

    def __post_init__(self) -> None:
        if type(self.network_access) is bool:
            object.__setattr__(
                self,
                "network_access",
                "allowed" if self.network_access else "denied",
            )
        if self._declaration_errors():
            raise CaptureError("capture_capability_invalid")

    @classmethod
    def compatibility_default(cls, provider: str) -> CaptureCapabilityManifest:
        """Keep old fetch-only adapters usable without claiming new hooks."""

        return cls(
            provider=provider,
            availability="partial",
            acquisition_mode="legacy",
            initial_snapshot=False,
            incremental=False,
            cursor_support=False,
            coverage="unavailable",
            coverage_reason="capability_manifest_missing",
            freshness="unknown",
            authorization="unknown",
            retry_policy=CaptureRetryPolicy(
                retryable_failures=False,
                max_attempts=1,
                rate_limit=CaptureRateLimitPolicy(mode="unavailable"),
            ),
            connection="unknown",
            disconnect_supported=False,
            source_deletion="unsupported",
            purge_coordination="unsupported",
            network_access="unknown",
            data_egress=None,
            health="degraded",
            health_diagnostics=("capability_manifest_missing",),
            legacy_compatibility=True,
        )

    @classmethod
    def unavailable(
        cls,
        provider: str,
        *,
        reason: str = "provider_unavailable",
        authorization: CaptureAuthorizationState = "unknown",
        connection: CaptureConnectionState = "unknown",
    ) -> CaptureCapabilityManifest:
        return cls(
            provider=provider,
            availability="unavailable",
            acquisition_mode="unavailable",
            initial_snapshot=False,
            incremental=False,
            cursor_support=False,
            coverage="unavailable",
            coverage_reason=reason,
            freshness="unavailable",
            authorization=authorization,
            connection=connection,
            network_access="unknown",
            data_egress=None,
            health="unavailable",
            health_diagnostics=(reason,),
        )

    @property
    def coverage_state(self) -> CaptureCoverageState:
        return self.coverage

    @property
    def freshness_state(self) -> CaptureFreshnessState:
        return self.freshness

    @property
    def authorization_state(self) -> CaptureAuthorizationState:
        return self.authorization

    @property
    def connection_state(self) -> CaptureConnectionState:
        return self.connection

    @property
    def health_state(self) -> CaptureHealthState:
        return self.health

    def _declaration_errors(self) -> tuple[str, ...]:
        errors: list[str] = []

        def choice(value: Any, allowed: set[str]) -> bool:
            return isinstance(value, str) and value in allowed

        def safe_id(value: Any, maximum: int) -> bool:
            try:
                _bounded_opaque_id(value, maximum=maximum)
            except CaptureError:
                return False
            return True

        def safe_text(value: Any, maximum: int) -> bool:
            try:
                _bounded_text(value, maximum=maximum, code="capture_capability_invalid")
            except CaptureError:
                return False
            return True

        if self.version != CAPTURE_CAPABILITY_MANIFEST_VERSION:
            errors.append("version")
        if not safe_id(self.provider, MAX_PROVIDER_CHARS):
            errors.append("provider")
        if not choice(self.availability, {"complete", "partial", "unavailable"}):
            errors.append("availability")
        if not choice(
            self.acquisition_mode,
            {
                "initial_snapshot",
                "incremental",
                "snapshot_and_incremental",
                "unavailable",
                "legacy",
            },
        ):
            errors.append("acquisition_mode")
        for value in (
            self.initial_snapshot,
            self.incremental,
            self.cursor_support,
            self.disconnect_supported,
            self.legacy_compatibility,
        ):
            if type(value) is not bool:
                errors.append("boolean")
                break
        if not choice(self.coverage, {"complete", "partial", "unavailable"}):
            errors.append("coverage")
        if self.coverage == "complete" and self.coverage_reason is not None:
            errors.append("coverage_reason")
        if self.coverage != "complete" and not safe_text(self.coverage_reason, MAX_ERROR_CHARS):
            errors.append("coverage_reason")
        if not choice(self.freshness, {"fresh", "stale", "unknown", "unavailable"}):
            errors.append("freshness")
        if not choice(
            self.authorization,
            {"authorized", "reauthorization_required", "unauthorized", "unknown"},
        ):
            errors.append("authorization")
        if self.credential_ref is not None and not safe_id(
            self.credential_ref, MAX_FINGERPRINT_CHARS
        ):
            errors.append("credential_ref")
        if not isinstance(self.retry_policy, CaptureRetryPolicy):
            errors.append("retry_policy")
        if not choice(self.connection, {"connected", "disconnected", "unknown"}):
            errors.append("connection")
        if not choice(self.source_deletion, {"coordinated", "unsupported"}):
            errors.append("source_deletion")
        if not choice(self.purge_coordination, {"coordinated", "external_only", "unsupported"}):
            errors.append("purge_coordination")
        if not choice(self.network_access, {"allowed", "denied", "unknown"}):
            errors.append("network_access")
        if self.data_egress is not None and (
            not isinstance(self.data_egress, tuple) or len(self.data_egress) > 16
        ):
            errors.append("data_egress")
        elif self.data_egress is not None:
            # Allowed network access may declare an empty or bounded known
            # egress tuple; denied access requires the known-empty tuple, and
            # unknown access pairs only with None so unknown is not mistaken
            # for zero egress.
            if self.network_access == "unknown":
                errors.append("network_egress_truth")
            if self.network_access == "denied" and self.data_egress:
                errors.append("data_egress")
            if any(
                not safe_id(destination, MAX_PROVIDER_CHARS) for destination in self.data_egress
            ):
                errors.append("data_egress")
        elif self.network_access != "unknown":
            errors.append("network_egress_truth")
        if not choice(self.health, {"healthy", "degraded", "unavailable"}):
            errors.append("health")
        if (
            not isinstance(self.health_diagnostics, tuple)
            or len(self.health_diagnostics) > 16
            or any(not safe_id(code, MAX_ERROR_CHARS) for code in self.health_diagnostics)
        ):
            errors.append("health_diagnostics")

        if self.legacy_compatibility:
            if (
                self.acquisition_mode != "legacy"
                or self.availability == "complete"
                or self.connection != "unknown"
                or self.network_access != "unknown"
                or self.data_egress is not None
            ):
                errors.append("legacy_compatibility")
        elif self.acquisition_mode == "legacy":
            errors.append("legacy_compatibility")
        elif self.acquisition_mode == "initial_snapshot" and not self.initial_snapshot:
            errors.append("initial_snapshot")
        elif self.acquisition_mode == "incremental" and (
            self.initial_snapshot or not self.incremental or not self.cursor_support
        ):
            errors.append("incremental")
        elif self.acquisition_mode == "snapshot_and_incremental" and (
            not self.initial_snapshot or not self.incremental or not self.cursor_support
        ):
            errors.append("snapshot_and_incremental")
        elif self.acquisition_mode == "unavailable" and (
            self.initial_snapshot or self.incremental or self.cursor_support
        ):
            errors.append("unavailable_acquisition")

        if self.incremental and not self.cursor_support:
            errors.append("cursor_support")
        if self.availability == "complete" and (
            self.coverage != "complete"
            or self.freshness != "fresh"
            or self.authorization != "authorized"
            or self.connection != "connected"
            or self.health != "healthy"
            or self.network_access == "unknown"
            or self.data_egress is None
        ):
            errors.append("complete_truth")
        if self.availability == "unavailable" and (
            self.coverage != "unavailable"
            or self.freshness not in {"unknown", "unavailable"}
            or self.health != "unavailable"
        ):
            errors.append("unavailable_truth")
        if self.coverage == "unavailable" and self.availability == "complete":
            errors.append("coverage_truth")
        if self.freshness == "unavailable" and self.coverage == "complete":
            errors.append("freshness_truth")
        if self.connection == "disconnected" and self.health == "healthy":
            errors.append("connection_truth")
        if self.legacy_compatibility and self.authorization == "authorized":
            errors.append("legacy_authorization")
        return tuple(dict.fromkeys(errors))

    def conformance(self) -> CaptureCapabilityConformance:
        errors = self._declaration_errors()
        warnings: list[str] = []
        if self.coverage == "partial":
            warnings.append("partial_coverage")
        if self.freshness in {"stale", "unknown"}:
            warnings.append("freshness_not_current")
        if self.authorization == "reauthorization_required":
            warnings.append("reauthorization_required")
        if self.authorization == "unknown":
            warnings.append("authorization_unknown")
        if self.connection == "disconnected":
            warnings.append("disconnected")
        if self.connection == "unknown":
            warnings.append("connection_unknown")
        if self.network_access == "unknown":
            warnings.append("network_posture_unknown")
        if self.data_egress is None:
            warnings.append("data_egress_unknown")
        if self.legacy_compatibility:
            warnings.append("legacy_compatibility")
        return CaptureCapabilityConformance(
            valid=not errors,
            errors=errors,
            warnings=tuple(warnings),
        )

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "version": self.version,
            "provider": self.provider,
            "availability": self.availability,
            "acquisition_mode": self.acquisition_mode,
            "initial_snapshot": self.initial_snapshot,
            "incremental": self.incremental,
            "cursor_support": self.cursor_support,
            "coverage": self.coverage,
            "coverage_reason": self.coverage_reason,
            "freshness": self.freshness,
            "authorization": self.authorization,
            "credential_ref": self.credential_ref,
            "retry_policy": self.retry_policy.model_dump(),
            "connection": self.connection,
            "disconnect_supported": self.disconnect_supported,
            "source_deletion": self.source_deletion,
            "purge_coordination": self.purge_coordination,
            "network_access": self.network_access,
            "data_egress": None if self.data_egress is None else list(self.data_egress),
            "health": self.health,
            "health_diagnostics": list(self.health_diagnostics),
            "legacy_compatibility": self.legacy_compatibility,
        }


def capture_executable_capability_error(
    manifest: CaptureCapabilityManifest,
) -> str | None:
    """Return the bounded reason that would prevent an adapter run.

    This is deliberately shared by the runner and all content-free readiness
    projections. Legacy fetch-only adapters retain their narrow compatibility
    exception; every current manifest must prove its network/egress posture
    before it is considered executable.
    """

    if manifest.availability == "unavailable" or manifest.health == "unavailable":
        return "capture_adapter_unavailable"
    if not manifest.legacy_compatibility and (
        manifest.network_access == "unknown" or manifest.data_egress is None
    ):
        return "capture_capability_invalid"
    if not manifest.legacy_compatibility and manifest.authorization == "unknown":
        return "capture_capability_invalid"
    if manifest.authorization == "reauthorization_required":
        return "capture_reauthorization_required"
    if manifest.authorization == "unauthorized":
        return "capture_authorization_unavailable"
    if not manifest.legacy_compatibility and manifest.connection == "unknown":
        return "capture_capability_invalid"
    if manifest.connection == "disconnected":
        return "capture_disconnected"
    return None


@dataclass(frozen=True, slots=True)
class CaptureRunResult:
    run_id: str
    source_id: str
    status: Literal["skipped", "completed", "failed"]
    error_code: str | None
    pages: int
    events: int
    applied_events: int
    duplicate_events: int
    failures: int
    retry_count: int
    lag_events: int
    lag_pages: int

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "run_id": self.run_id,
            "source_id": self.source_id,
            "status": self.status,
            "error_code": self.error_code,
            "pages": self.pages,
            "events": self.events,
            "applied_events": self.applied_events,
            "duplicate_events": self.duplicate_events,
            "failures": self.failures,
            "retry_count": self.retry_count,
            "lag_events": self.lag_events,
            "lag_pages": self.lag_pages,
        }


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _lease_expiry(now: str) -> str:
    return (
        (_parse_time(now) + timedelta(seconds=LEASE_SECONDS))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _numeric_order(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def _order_before(left: str, right: str) -> bool:
    left_number = _numeric_order(left)
    right_number = _numeric_order(right)
    if left_number is not None and right_number is not None:
        return left_number < right_number
    return left < right


def _order_gap(previous: str, current: str) -> bool:
    previous_number = _numeric_order(previous)
    current_number = _numeric_order(current)
    return (
        previous_number is not None
        and current_number is not None
        and current_number > previous_number + 1
    )


def _canonical_lineage(source_id: str, provider_item_id: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{provider_item_id}".encode()).hexdigest()
    return f"capture-lineage-{digest}"


def _idempotency_key(source_id: str, provider_event_id: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{provider_event_id}".encode()).hexdigest()
    return f"capture-event-{digest}"


_CAPTURE_MIGRATION_PATHS = (
    Path(__file__).parent / "migrations" / "core" / "015_continuous_capture.sql",
    Path(__file__).parent / "migrations" / "core" / "016_registered_source_admission.sql",
    Path(__file__).parent / "migrations" / "core" / "017_capture_page_recovery.sql",
    Path(__file__).parent / "migrations" / "core" / "018_client_capture_source.sql",
)
_CAPTURE_MIGRATION_VERSIONS = frozenset(
    int(path.name.split("_", 1)[0]) for path in _CAPTURE_MIGRATION_PATHS
)


def ensure_capture_schema(connection: Any, *, through_version: int | None = None) -> None:
    """Repair capture objects through a known-applied migration version."""

    if through_version is not None and through_version not in _CAPTURE_MIGRATION_VERSIONS:
        raise ValueError("capture repair version must be an applied capture migration")
    full_repair = through_version is None

    for migration_path in _CAPTURE_MIGRATION_PATHS:
        version = int(migration_path.name.split("_", 1)[0])
        if through_version is not None and version > through_version:
            break
        migration = migration_path.read_text(encoding="utf-8")
        for statement in _migration_statements(migration):
            # Migration 018 deliberately relaxes the older one-observation
            # index so one lifecycle event can retain raw and formed evidence.
            # A full repair must not recreate that obsolete unique index before
            # migration 018 has a chance to drop it; this also keeps repair safe
            # for databases that already contain both projections.
            if (
                full_repair
                and version == 16
                and re.search(
                    r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+"
                    r"uq_context_candidates_capture_event",
                    statement,
                    flags=re.IGNORECASE,
                )
            ):
                continue
            added_column = _added_column(statement)
            if added_column is not None:
                table, column = added_column
                columns = {
                    str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
                }
                if column in columns:
                    continue
            connection.execute(statement)


class CaptureLedger:
    """Typed repository for migration-015 capture state."""

    def __init__(
        self,
        store: CoreStore,
        *,
        clock: Callable[[], str] = utc_now,
        activity_gate: CoreActivityGate | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.activity_gate = activity_gate or CoreActivityGate()

    @staticmethod
    def _scopes_from_row(row: Any) -> tuple[str, ...]:
        try:
            raw_scopes = json.loads(str(row["requested_scopes_json"]))
        except (TypeError, UnicodeError, ValueError):
            raise CaptureError("capture_page_malformed") from None
        if not isinstance(raw_scopes, list) or len(raw_scopes) > MAX_SCOPE_COUNT:
            raise CaptureError("capture_page_malformed") from None
        try:
            return tuple(_bounded_opaque_id(item, maximum=MAX_SCOPE_CHARS) for item in raw_scopes)
        except CaptureError:
            raise CaptureError("capture_page_malformed") from None

    @staticmethod
    def _source_from_row(row: Any) -> CaptureSource:
        scopes = CaptureLedger._scopes_from_row(row)
        return CaptureSource(
            id=str(row["id"]),
            provider=str(row["provider"]),
            account_label=str(row["account_label"]),
            account_fingerprint=cast(str | None, row["account_fingerprint"]),
            requested_scopes=scopes,
            local_only=bool(row["local_only"]),
            local_only_acknowledged=bool(row["local_only_acknowledged"]),
            lifecycle_state=cast(CaptureLifecycleState, row["lifecycle_state"]),
            retry_count=int(row["retry_count"]),
            next_retry_at=cast(str | None, row["next_retry_at"]),
            last_error_code=cast(str | None, row["last_error_code"]),
            last_error_at=cast(str | None, row["last_error_at"]),
            lag_events=int(row["lag_events"]),
            lag_pages=int(row["lag_pages"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_run_at=cast(str | None, row["last_run_at"]),
        )

    @staticmethod
    def _public_source(source: CaptureSource) -> dict[str, Any]:
        return source.model_dump()

    def create_source(
        self,
        *,
        provider: str,
        account_label: str,
        account_fingerprint: str | None = None,
        requested_scopes: Sequence[str] = (),
        local_only_acknowledged: bool = False,
    ) -> CaptureSource:
        provider = _bounded_opaque_id(provider, maximum=MAX_PROVIDER_CHARS)
        label = _bounded_text(account_label, maximum=MAX_ACCOUNT_LABEL_CHARS)
        fingerprint = (
            _bounded_opaque_id(account_fingerprint, maximum=MAX_FINGERPRINT_CHARS)
            if account_fingerprint is not None
            else None
        )
        if isinstance(requested_scopes, (str, bytes)) or len(requested_scopes) > MAX_SCOPE_COUNT:
            raise CaptureError("capture_page_malformed")
        scopes = tuple(
            _bounded_opaque_id(scope, maximum=MAX_SCOPE_CHARS) for scope in requested_scopes
        )
        now = self.clock()
        source_id = new_id()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO capture_sources"
                "(id,vault_id,provider,account_label,account_fingerprint,requested_scopes_json,"
                "local_only,local_only_acknowledged,lifecycle_state,credential_ref,retry_count,"
                "lag_events,lag_pages,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    self.store.vault_id(),
                    provider,
                    label,
                    fingerprint,
                    json.dumps(scopes, separators=(",", ":")),
                    1,
                    int(local_only_acknowledged),
                    "disabled",
                    None,
                    0,
                    0,
                    0,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO capture_checkpoints(source_id,generation,updated_at) VALUES(?,?,?)",
                (source_id, 0, now),
            )
            row = connection.execute(
                "SELECT * FROM capture_sources WHERE id=?", (source_id,)
            ).fetchone()
            assert row is not None
            return self._source_from_row(row)

    def get_source(self, source_id: str) -> CaptureSource:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM capture_sources WHERE id=?", (source_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("capture source not found")
        return self._source_from_row(row)

    def list_sources(self, *, limit: int = 100, offset: int = 0) -> tuple[list[CaptureSource], int]:
        if type(limit) is not int or type(offset) is not int:
            raise CaptureError("capture_page_malformed")
        bounded_limit = min(max(limit, 1), 500)
        bounded_offset = max(offset, 0)
        with self.store.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM capture_sources").fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM capture_sources ORDER BY created_at,id LIMIT ? OFFSET ?",
                (bounded_limit, bounded_offset),
            ).fetchall()
        return [self._source_from_row(row) for row in rows], total

    def transition(self, source_id: str, target: CaptureLifecycleState) -> CaptureSource:
        allowed: dict[CaptureLifecycleState, frozenset[CaptureLifecycleState]] = {
            "disabled": frozenset({"enabled", "revoked"}),
            "enabled": frozenset({"paused", "disabled", "degraded", "reconciling", "revoked"}),
            "paused": frozenset({"enabled", "disabled", "revoked"}),
            "degraded": frozenset({"reconciling", "paused", "disabled", "revoked"}),
            "reconciling": frozenset({"enabled", "degraded", "paused", "revoked"}),
            "revoked": frozenset(),
        }
        now = self.clock()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM capture_sources WHERE id=?", (source_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("capture source not found")
            current = cast(CaptureLifecycleState, row["lifecycle_state"])
            if target not in allowed[current]:
                raise CaptureTransitionError()
            if target in {"enabled", "reconciling"} and (
                not bool(row["local_only"]) or not bool(row["local_only_acknowledged"])
            ):
                raise CaptureError("capture_local_only_required")
            credential_ref = None if target == "revoked" else row["credential_ref"]
            if current == "reconciling" and target != "reconciling":
                connection.execute(
                    "UPDATE capture_runs SET state='abandoned',error_code=?,completed_at=? "
                    "WHERE source_id=? AND state='running'",
                    ("capture_invalid_transition", now, source_id),
                )
            connection.execute(
                "UPDATE capture_sources SET lifecycle_state=?,credential_ref=?,updated_at=? WHERE id=?",
                (target, credential_ref, now, source_id),
            )
            refreshed = connection.execute(
                "SELECT * FROM capture_sources WHERE id=?", (source_id,)
            ).fetchone()
            assert refreshed is not None
            return self._source_from_row(refreshed)

    def _checkpoint(self, source_id: str) -> dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT generation,last_order_key,last_event_id,cursor,updated_at,"
                "pending_generation,pending_cursor,pending_event_ids_json "
                "FROM capture_checkpoints WHERE source_id=?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("capture checkpoint not found")
        return {
            key: row[key]
            for key in (
                "generation",
                "last_order_key",
                "last_event_id",
                "cursor",
                "updated_at",
                "pending_generation",
                "pending_cursor",
                "pending_event_ids_json",
            )
        }

    @staticmethod
    def _require_active_run(connection: Any, handle: CaptureRunHandle, now: str) -> None:
        if not isinstance(handle, CaptureRunHandle):
            raise CaptureError("capture_lease_expired")
        owned = connection.execute(
            "SELECT 1 FROM capture_runs AS r "
            "JOIN capture_sources AS s ON s.id=r.source_id "
            "WHERE r.id=? AND r.source_id=? AND r.lease_token=? "
            "AND r.state='running' AND r.lease_expires_at>? "
            "AND s.lifecycle_state='reconciling'",
            (handle.run_id, handle.source_id, handle.lease_token, now),
        ).fetchone()
        if owned is None:
            raise CaptureError("capture_lease_expired")

    def recover_expired_runs(self) -> int:
        now = self.clock()
        changed = 0
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT id,source_id FROM capture_runs WHERE state='running' AND lease_expires_at<=?",
                (now,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE capture_runs SET state='abandoned',error_code=?,completed_at=? WHERE id=?",
                    ("capture_lease_expired", now, row["id"]),
                )
                connection.execute(
                    "UPDATE capture_sources SET lifecycle_state='degraded',last_error_code=?,"
                    "last_error_at=?,updated_at=? WHERE id=? AND lifecycle_state='reconciling'",
                    ("capture_lease_expired", now, now, row["source_id"]),
                )
                changed += 1
        return changed

    def active_lease_snapshot(self) -> dict[str, Any]:
        """Return bounded, content-free durable capture lease activity."""

        now = self.clock()
        vault_id = self.store.vault_id()
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT 1 FROM capture_runs AS r "
                "JOIN capture_sources AS s ON s.id=r.source_id "
                "WHERE s.vault_id=? AND r.state='running' AND r.lease_expires_at>? "
                "LIMIT ?",
                (vault_id, now, MAX_ACTIVITY_SNAPSHOT_ITEMS + 1),
            ).fetchall()
        truncated = len(rows) > MAX_ACTIVITY_SNAPSHOT_ITEMS
        count = min(len(rows), MAX_ACTIVITY_SNAPSHOT_ITEMS)
        return {
            "active": count > 0,
            "count": count,
            "truncated": truncated,
        }

    def begin_run(self, source_id: str) -> tuple[CaptureRunHandle, CaptureSource, int]:
        with self.activity_gate.activity():
            return self._begin_run(source_id)

    def _begin_run(self, source_id: str) -> tuple[CaptureRunHandle, CaptureSource, int]:
        self.recover_expired_runs()
        now = self.clock()
        run_id = new_id()
        lease_token = secrets.token_urlsafe(18)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM capture_sources WHERE id=?", (source_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("capture source not found")
            source = self._source_from_row(row)
            if source.lifecycle_state in {"disabled", "paused", "revoked"}:
                raise CaptureError("capture_source_not_enabled")
            if source.lifecycle_state == "degraded":
                raise CaptureError("capture_source_degraded")
            active = connection.execute(
                "SELECT 1 FROM capture_runs WHERE source_id=? AND state='running' AND lease_expires_at>? LIMIT 1",
                (source_id, now),
            ).fetchone()
            if active is not None:
                raise CaptureError("capture_run_in_progress")
            attempt = source.retry_count + 1
            connection.execute(
                "INSERT INTO capture_runs"
                "(id,source_id,state,lease_token,lease_expires_at,attempt_count,started_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    source_id,
                    "running",
                    lease_token,
                    _lease_expiry(now),
                    attempt,
                    now,
                ),
            )
            connection.execute(
                "UPDATE capture_sources SET lifecycle_state='reconciling',last_run_at=?,updated_at=? WHERE id=?",
                (now, now, source_id),
            )
        return (
            CaptureRunHandle._mint(run_id, source_id, lease_token),
            self.get_source(source_id),
            attempt,
        )

    def renew_run(self, handle: CaptureRunHandle) -> CaptureRunHandle:
        """Extend a bounded foreground lease only while its capability is valid."""

        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            updated = connection.execute(
                "UPDATE capture_runs SET lease_expires_at=? "
                "WHERE id=? AND source_id=? AND lease_token=? AND state='running' "
                "AND lease_expires_at>?",
                (
                    _lease_expiry(now),
                    handle.run_id,
                    handle.source_id,
                    handle.lease_token,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise CaptureError("capture_lease_expired")
        return handle

    @staticmethod
    def _pending_event_ids(value: Any) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise CaptureError("capture_failed")
        try:
            if len(value.encode("utf-8")) > MAX_PENDING_EVENT_IDS_BYTES:
                raise CaptureError("capture_failed")
        except UnicodeError:
            raise CaptureError("capture_failed") from None
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            raise CaptureError("capture_failed") from None
        if (
            not isinstance(decoded, list)
            or len(decoded) > MAX_PAGE_EVENTS
            or any(not isinstance(item, str) for item in decoded)
        ):
            raise CaptureError("capture_failed")
        raw_event_ids = tuple(decoded)
        try:
            for event_id in raw_event_ids:
                _bounded_opaque_id(event_id, maximum=MAX_EVENT_ID_CHARS)
        except CaptureError:
            raise CaptureError("capture_failed") from None
        event_ids = tuple(dict.fromkeys(raw_event_ids))
        encoded = json.dumps(event_ids, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PENDING_EVENT_IDS_BYTES:
            raise CaptureError("capture_failed")
        return event_ids

    @classmethod
    def _pending_from_checkpoint(
        cls, checkpoint: Mapping[str, Any]
    ) -> tuple[int, str | None, tuple[str, ...]] | None:
        generation = checkpoint["pending_generation"]
        cursor = checkpoint["pending_cursor"]
        event_ids_json = checkpoint["pending_event_ids_json"]
        if generation is None:
            if cursor is not None or event_ids_json is not None:
                raise CaptureError("capture_failed")
            return None
        if type(generation) is not int or not 0 <= generation <= MAX_CAPTURE_INTEGER:
            raise CaptureError("capture_failed")
        if cursor is not None:
            if not isinstance(cursor, str):
                raise CaptureError("capture_failed")
            try:
                cursor = _bounded_cursor(cursor)
            except CaptureError:
                raise CaptureError("capture_failed") from None
        event_ids = cls._pending_event_ids(event_ids_json)
        if event_ids is None:
            raise CaptureError("capture_failed")
        return generation, cast(str | None, cursor), event_ids

    @staticmethod
    def _stage_event_tx(
        connection: Any,
        *,
        source_id: str,
        event: CaptureEvent,
        now: str,
        idempotency_key: str | None = None,
    ) -> tuple[str, bool, int]:
        payload_json, payload_hash = event.normalized()
        durable_idempotency_key = (
            _idempotency_key(source_id, event.provider_event_id)
            if idempotency_key is None
            else _bounded_opaque_id(idempotency_key, maximum=128)
        )
        claimed = connection.execute(
            "SELECT source_id,provider_event_id FROM capture_events WHERE idempotency_key=?",
            (durable_idempotency_key,),
        ).fetchone()
        if claimed is not None and (
            str(claimed["source_id"]) != source_id
            or str(claimed["provider_event_id"]) != event.provider_event_id
        ):
            raise CaptureError("capture_event_payload_conflict")
        existing = connection.execute(
            "SELECT id,status,payload_hash,provider_item_id,operation,generation,order_key,attempts,"
            "idempotency_key "
            "FROM capture_events "
            "WHERE source_id=? AND provider_event_id=?",
            (source_id, event.provider_event_id),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["payload_hash"]) != payload_hash
                or str(existing["provider_item_id"]) != event.provider_item_id
                or str(existing["operation"]) != event.operation
                or int(existing["generation"]) != event.generation
                or str(existing["order_key"]) != event.order_key
                or str(existing["idempotency_key"]) != durable_idempotency_key
            ):
                raise CaptureError("capture_event_payload_conflict")
            if str(existing["status"]) == "applied":
                return str(existing["id"]), True, int(existing["attempts"])
            attempts = int(existing["attempts"]) + 1
            connection.execute(
                "UPDATE capture_events SET status='staged',attempts=?,error_code=NULL WHERE id=?",
                (attempts, existing["id"]),
            )
            return str(existing["id"]), False, attempts
        event_id = new_id()
        connection.execute(
            "INSERT INTO capture_events"
            "(id,source_id,provider_event_id,provider_item_id,generation,order_key,operation,"
            "normalized_payload_json,payload_hash,status,attempts,idempotency_key,received_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'staged',1,?,?)",
            (
                event_id,
                source_id,
                event.provider_event_id,
                event.provider_item_id,
                event.generation,
                event.order_key,
                event.operation,
                payload_json,
                payload_hash,
                durable_idempotency_key,
                now,
            ),
        )
        return event_id, False, 1

    def stage_event(self, handle: CaptureRunHandle, event: CaptureEvent) -> tuple[str, bool, int]:
        """Stage one event for compatibility with existing callers."""

        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            checkpoint = connection.execute(
                "SELECT pending_generation,pending_cursor,pending_event_ids_json "
                "FROM capture_checkpoints WHERE source_id=?",
                (handle.source_id,),
            ).fetchone()
            if checkpoint is None:
                raise ConflictError("capture checkpoint disappeared before event staging")
            if self._pending_from_checkpoint(checkpoint) is not None:
                raise CaptureError("capture_retryable_failure")
            return self._stage_event_tx(
                connection,
                source_id=handle.source_id,
                event=event,
                now=now,
            )

    def stage_page(
        self,
        handle: CaptureRunHandle,
        page: CapturePage,
    ) -> tuple[tuple[str, bool, int], ...]:
        """Atomically stage a complete validated page and its recovery marker."""

        if not isinstance(page, CapturePage):
            raise CaptureError("capture_page_malformed")
        provider_event_ids = tuple(event.provider_event_id for event in page.events)
        if len(set(provider_event_ids)) != len(provider_event_ids):
            raise CaptureError("capture_event_payload_conflict")
        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            checkpoint = connection.execute(
                "SELECT pending_generation,pending_cursor,pending_event_ids_json "
                "FROM capture_checkpoints WHERE source_id=?",
                (handle.source_id,),
            ).fetchone()
            if checkpoint is None:
                raise ConflictError("capture checkpoint disappeared before page staging")
            if self._pending_from_checkpoint(checkpoint) is not None:
                raise CaptureError("capture_retryable_failure")
            staged = tuple(
                self._stage_event_tx(
                    connection,
                    source_id=handle.source_id,
                    event=event,
                    now=now,
                )
                for event in page.events
            )
            event_ids = tuple(item[0] for item in staged)
            if len(set(event_ids)) != len(event_ids):
                raise CaptureError("capture_event_payload_conflict")
            pending_event_ids_json = json.dumps(event_ids, separators=(",", ":"))
            if len(pending_event_ids_json.encode("utf-8")) > MAX_PENDING_EVENT_IDS_BYTES:
                raise CaptureError("capture_event_limit_exceeded")
            connection.execute(
                "UPDATE capture_checkpoints SET pending_generation=?,pending_cursor=?,"
                "pending_event_ids_json=?,updated_at=? WHERE source_id=?",
                (
                    page.generation,
                    page.next_cursor,
                    pending_event_ids_json,
                    now,
                    handle.source_id,
                ),
            )
            return staged

    def _retry_pending_event(self, handle: CaptureRunHandle, event_id: str) -> bool:
        """Prepare one durable pending event and report whether it was applied."""

        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            row = connection.execute(
                "SELECT status,attempts FROM capture_events WHERE id=? AND source_id=?",
                (event_id, handle.source_id),
            ).fetchone()
            if row is None:
                raise ConflictError("capture event unavailable for pending retry")
            if str(row["status"]) == "applied":
                return True
            connection.execute(
                "UPDATE capture_events SET status='staged',attempts=?,error_code=NULL "
                "WHERE id=? AND source_id=?",
                (int(row["attempts"]) + 1, event_id, handle.source_id),
            )
            return False

    def commit_event(
        self,
        *,
        handle: CaptureRunHandle,
        event: CaptureEvent,
        event_id: str,
        receipt: str,
        canonical_record_id: str,
    ) -> None:
        receipt = _bounded_opaque_id(receipt, maximum=MAX_ERROR_CHARS)
        canonical_record_id = _bounded_opaque_id(canonical_record_id, maximum=MAX_EVENT_ID_CHARS)
        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            stored = connection.execute(
                "SELECT status,provider_event_id,provider_item_id,operation,generation,order_key,payload_hash "
                "FROM capture_events WHERE id=? AND source_id=?",
                (event_id, handle.source_id),
            ).fetchone()
            if stored is None:
                raise ConflictError("capture event disappeared before commit")
            identity_matches = (
                str(stored["provider_event_id"]) == event.provider_event_id
                and str(stored["provider_item_id"]) == event.provider_item_id
                and str(stored["operation"]) == event.operation
                and int(stored["generation"]) == event.generation
                and str(stored["order_key"]) == event.order_key
            )
            payload_matches = str(stored["payload_hash"]) == event.normalized()[1]
            vault_id = CoreStore._vault_id_tx(connection)
            purged = (
                connection.execute(
                    "SELECT 1 FROM purge_tombstones WHERE vault_id=? AND target_type='record' "
                    "AND stable_id=?",
                    (vault_id, canonical_record_id),
                ).fetchone()
                is not None
            )
            if not identity_matches or (not payload_matches and not purged):
                raise CaptureError("capture_event_payload_conflict")
            prior_item = connection.execute(
                "SELECT canonical_record_id FROM capture_items WHERE source_id=? AND provider_item_id=?",
                (handle.source_id, event.provider_item_id),
            ).fetchone()
            if (
                prior_item is not None
                and str(prior_item["canonical_record_id"]) != canonical_record_id
            ):
                raise CaptureError("capture_lineage_conflict")
            connection.execute(
                "UPDATE capture_events SET status='applied',application_receipt=?,error_code=NULL,applied_at=? "
                "WHERE id=? AND source_id=?",
                (receipt, now, event_id, handle.source_id),
            )
            connection.execute(
                "INSERT INTO capture_items"
                "(source_id,provider_item_id,canonical_record_id,generation,last_event_id,item_state,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id,provider_item_id) DO UPDATE SET "
                "canonical_record_id=excluded.canonical_record_id,generation=excluded.generation,"
                "last_event_id=excluded.last_event_id,item_state=excluded.item_state,updated_at=excluded.updated_at",
                (
                    handle.source_id,
                    event.provider_item_id,
                    canonical_record_id,
                    event.generation,
                    event_id,
                    "deleted" if event.operation == "delete" else "active",
                    now,
                ),
            )
            checkpoint = connection.execute(
                "SELECT generation,last_order_key FROM capture_checkpoints WHERE source_id=?",
                (handle.source_id,),
            ).fetchone()
            if checkpoint is None:
                raise ConflictError("capture checkpoint disappeared before commit")
            current_generation = int(checkpoint["generation"])
            current_order = cast(str | None, checkpoint["last_order_key"])
            advance = current_order is None or event.generation > current_generation
            if event.generation == current_generation and current_order is not None:
                advance = _order_before(current_order, event.order_key)
            if advance:
                connection.execute(
                    "UPDATE capture_checkpoints SET generation=?,last_order_key=?,last_event_id=?,updated_at=? "
                    "WHERE source_id=?",
                    (event.generation, event.order_key, event_id, now, handle.source_id),
                )

    def _commit_pending_page_tx(
        self,
        connection: Any,
        *,
        handle: CaptureRunHandle,
        page: CapturePage,
        now: str,
    ) -> bool:
        checkpoint = connection.execute(
            "SELECT generation,cursor,last_order_key,pending_generation,pending_cursor,"
            "pending_event_ids_json FROM capture_checkpoints WHERE source_id=?",
            (handle.source_id,),
        ).fetchone()
        if checkpoint is None:
            raise ConflictError("capture checkpoint disappeared before page commit")
        pending = self._pending_from_checkpoint(checkpoint)
        if pending is None:
            return False
        pending_generation, pending_cursor, event_ids = pending
        if pending_generation != page.generation or pending_cursor != page.next_cursor:
            raise CaptureError("capture_event_generation_mismatch")
        for event_id in event_ids:
            event_row = connection.execute(
                "SELECT status FROM capture_events WHERE id=? AND source_id=?",
                (event_id, handle.source_id),
            ).fetchone()
            if event_row is None:
                raise ConflictError("capture event disappeared before page commit")
            if str(event_row["status"]) != "applied":
                raise CaptureError("capture_retryable_failure")
        current_generation = int(checkpoint["generation"])
        if page.generation < current_generation:
            raise CaptureError("capture_event_generation_mismatch")
        previous_cursor = cast(str | None, checkpoint["cursor"])
        if previous_cursor == page.next_cursor and not page.done:
            raise CaptureError("capture_invalid_cursor")
        if page.generation > current_generation and not event_ids:
            connection.execute(
                "UPDATE capture_checkpoints SET generation=?,cursor=?,last_order_key=NULL,"
                "last_event_id=NULL,pending_generation=NULL,pending_cursor=NULL,"
                "pending_event_ids_json=NULL,updated_at=? WHERE source_id=?",
                (page.generation, page.next_cursor, now, handle.source_id),
            )
        else:
            connection.execute(
                "UPDATE capture_checkpoints SET generation=?,cursor=?,pending_generation=NULL,"
                "pending_cursor=NULL,pending_event_ids_json=NULL,updated_at=? WHERE source_id=?",
                (page.generation, page.next_cursor, now, handle.source_id),
            )
        return True

    def commit_page_cursor(self, handle: CaptureRunHandle, page: CapturePage) -> None:
        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            if self._commit_pending_page_tx(
                connection,
                handle=handle,
                page=page,
                now=now,
            ):
                return
            row = connection.execute(
                "SELECT generation,cursor,last_order_key,last_event_id FROM capture_checkpoints "
                "WHERE source_id=?",
                (handle.source_id,),
            ).fetchone()
            if row is None:
                raise ConflictError("capture checkpoint disappeared before page commit")
            if page.generation < int(row["generation"]):
                return
            previous_cursor = cast(str | None, row["cursor"])
            if previous_cursor == page.next_cursor and not page.done:
                raise CaptureError("capture_invalid_cursor")
            if page.generation > int(row["generation"]) and not page.events:
                connection.execute(
                    "UPDATE capture_checkpoints SET generation=?,cursor=?,last_order_key=NULL,"
                    "last_event_id=NULL,updated_at=? WHERE source_id=?",
                    (page.generation, page.next_cursor, now, handle.source_id),
                )
            else:
                connection.execute(
                    "UPDATE capture_checkpoints SET generation=?,cursor=?,updated_at=? "
                    "WHERE source_id=?",
                    (page.generation, page.next_cursor, now, handle.source_id),
                )

    def mark_event_failure(self, handle: CaptureRunHandle, event_id: str, code: str) -> None:
        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            updated = connection.execute(
                "UPDATE capture_events SET status='staged',error_code=? "
                "WHERE id=? AND source_id=? AND status!='applied'",
                (code[:MAX_ERROR_CHARS], event_id, handle.source_id),
            )
            if updated.rowcount != 1:
                event_row = connection.execute(
                    "SELECT status FROM capture_events WHERE id=? AND source_id=?",
                    (event_id, handle.source_id),
                ).fetchone()
                if event_row is None:
                    raise ConflictError("capture event unavailable for failure mark")

    def finish_run(
        self,
        *,
        handle: CaptureRunHandle,
        status: Literal["completed", "failed"],
        error_code: str | None,
        pages: int,
        events: int,
        applied_events: int,
        duplicate_events: int,
        failures: int,
        attempts: int,
        backoff: BackoffPolicy,
    ) -> CaptureRunResult:
        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            source_row = connection.execute(
                "SELECT retry_count FROM capture_sources WHERE id=?", (handle.source_id,)
            ).fetchone()
            if source_row is None:
                raise NotFoundError("capture source not found")
            current_retry = int(source_row["retry_count"])
            next_retry: str | None = None
            retry_count = 0 if status == "completed" else current_retry + 1
            if status == "failed":
                next_retry = (
                    (_parse_time(now) + timedelta(seconds=backoff.delay_seconds(retry_count)))
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
            lag_events = int(
                connection.execute(
                    "SELECT COUNT(*) FROM capture_events WHERE source_id=? AND status!='applied'",
                    (handle.source_id,),
                ).fetchone()[0]
            )
            pending_page = connection.execute(
                "SELECT pending_generation FROM capture_checkpoints WHERE source_id=?",
                (handle.source_id,),
            ).fetchone()
            lag_pages = (
                1 if lag_events or (pending_page is not None and pending_page[0] is not None) else 0
            )
            run_state: CaptureRunState = "completed" if status == "completed" else "failed"
            updated_run = connection.execute(
                "UPDATE capture_runs SET state=?,page_count=?,event_count=?,applied_event_count=?,"
                "duplicate_event_count=?,failure_count=?,error_code=?,completed_at=? "
                "WHERE id=? AND source_id=? AND lease_token=? AND state='running' "
                "AND lease_expires_at>?",
                (
                    run_state,
                    pages,
                    events,
                    applied_events,
                    duplicate_events,
                    failures,
                    error_code,
                    now,
                    handle.run_id,
                    handle.source_id,
                    handle.lease_token,
                    now,
                ),
            )
            if updated_run.rowcount != 1:
                raise CaptureError("capture_lease_expired")
            updated_source = connection.execute(
                "UPDATE capture_sources SET lifecycle_state=?,retry_count=?,next_retry_at=?,last_error_code=?,"
                "last_error_at=?,lag_events=?,lag_pages=?,updated_at=? "
                "WHERE id=? AND lifecycle_state='reconciling' AND EXISTS ("
                "SELECT 1 FROM capture_runs WHERE id=? AND source_id=? AND lease_token=? "
                "AND state=? AND lease_expires_at>?)",
                (
                    "enabled" if status == "completed" else "degraded",
                    retry_count,
                    next_retry,
                    error_code,
                    now if error_code else None,
                    lag_events,
                    lag_pages,
                    now,
                    handle.source_id,
                    handle.run_id,
                    handle.source_id,
                    handle.lease_token,
                    "completed" if status == "completed" else "failed",
                    now,
                ),
            )
            if updated_source.rowcount != 1:
                raise CaptureError("capture_lease_expired")
        return CaptureRunResult(
            run_id=handle.run_id,
            source_id=handle.source_id,
            status=status,
            error_code=error_code,
            pages=pages,
            events=events,
            applied_events=applied_events,
            duplicate_events=duplicate_events,
            failures=failures,
            retry_count=retry_count,
            lag_events=lag_events,
            lag_pages=lag_pages,
        )

    def recover_adapter_unavailable(self, source_id: str) -> CaptureSource:
        """Reset one adapter-unavailable retry state after runtime recovery."""

        now = self.clock()
        with self.store.transaction() as connection:
            updated = connection.execute(
                "UPDATE capture_sources SET lifecycle_state='enabled',retry_count=0,"
                "next_retry_at=NULL,last_error_code=NULL,last_error_at=NULL,updated_at=? "
                "WHERE id=? AND lifecycle_state='degraded' "
                "AND last_error_code='capture_adapter_unavailable'",
                (now, source_id),
            )
            if updated.rowcount not in {0, 1}:
                raise CaptureError("capture_failed")
        return self.get_source(source_id)

    def stale_result(
        self,
        handle: CaptureRunHandle,
        *,
        pages: int,
        events: int,
        applied_events: int,
        duplicate_events: int,
        failures: int,
    ) -> CaptureRunResult:
        """Return a bounded result after ownership loss without writing state."""

        source = self.get_source(handle.source_id)
        return CaptureRunResult(
            run_id=handle.run_id,
            source_id=handle.source_id,
            status="failed",
            error_code="capture_lease_expired",
            pages=pages,
            events=events,
            applied_events=applied_events,
            duplicate_events=duplicate_events,
            failures=failures,
            retry_count=source.retry_count,
            lag_events=source.lag_events,
            lag_pages=source.lag_pages,
        )

    def skipped_result(self, source_id: str, code: str) -> CaptureRunResult:
        source = self.get_source(source_id)
        return CaptureRunResult(
            run_id="not-started",
            source_id=source_id,
            status="skipped",
            error_code=code,
            pages=0,
            events=0,
            applied_events=0,
            duplicate_events=0,
            failures=0,
            retry_count=source.retry_count,
            lag_events=source.lag_events,
            lag_pages=source.lag_pages,
        )

    def status(self, source_id: str) -> dict[str, Any]:
        source = self.get_source(source_id)
        with self.store.connect() as connection:
            run = connection.execute(
                "SELECT state,attempt_count,page_count,event_count,applied_event_count,"
                "duplicate_event_count,failure_count,error_code,started_at,completed_at "
                "FROM capture_runs WHERE source_id=? ORDER BY started_at DESC,id DESC LIMIT 1",
                (source_id,),
            ).fetchone()
        result: dict[str, Any] = {"source": source.model_dump(), "checkpoint": {"generation": 0}}
        if run is not None:
            result["last_run"] = {
                "state": str(run["state"]),
                "attempt_count": int(run["attempt_count"]),
                "pages": int(run["page_count"]),
                "events": int(run["event_count"]),
                "applied_events": int(run["applied_event_count"]),
                "duplicate_events": int(run["duplicate_event_count"]),
                "failures": int(run["failure_count"]),
                "error_code": cast(str | None, run["error_code"]),
                "started_at": str(run["started_at"]),
                "completed_at": cast(str | None, run["completed_at"]),
            }
        with self.store.connect() as connection:
            checkpoint = connection.execute(
                "SELECT generation FROM capture_checkpoints WHERE source_id=?", (source_id,)
            ).fetchone()
        if checkpoint is not None:
            result["checkpoint"] = {"generation": int(checkpoint["generation"])}
        return result


class CaptureCoordinator:
    """Single foreground capture run coordinator."""

    def __init__(
        self,
        store: CoreStore,
        *,
        sink: CaptureApplicationSink | None = None,
        backoff: BackoffPolicy | None = None,
        clock: Callable[[], str] = utc_now,
        activity_gate: CoreActivityGate | None = None,
    ) -> None:
        self.activity_gate = activity_gate or CoreActivityGate()
        self.ledger = CaptureLedger(store, clock=clock, activity_gate=self.activity_gate)
        self.sink = sink
        self.backoff = backoff or BackoffPolicy()
        self.adapters: dict[str, CaptureProviderAdapter] = {}
        self.clock = clock
        self._run_lock = threading.Lock()

    def register_adapter(self, provider: str, adapter: CaptureProviderAdapter) -> None:
        self.adapters[_bounded_opaque_id(provider, maximum=MAX_PROVIDER_CHARS)] = adapter

    def create_source(self, **kwargs: Any) -> CaptureSource:
        return self.ledger.create_source(**kwargs)

    def get_source(self, source_id: str) -> CaptureSource:
        return self.ledger.get_source(source_id)

    def list_sources(self, *, limit: int = 100, offset: int = 0) -> tuple[list[CaptureSource], int]:
        return self.ledger.list_sources(limit=limit, offset=offset)

    def status(self, source_id: str) -> dict[str, Any]:
        return self.ledger.status(source_id)

    def activity_snapshot(self) -> dict[str, Any]:
        """Return bounded, content-free foreground and durable run activity."""

        leases = self.ledger.active_lease_snapshot()
        return {
            "foreground_run_active": self._run_lock.locked(),
            "durable_lease_active": bool(leases["active"]),
            "durable_lease_count": int(leases["count"]),
            "durable_lease_truncated": bool(leases["truncated"]),
        }

    def capability_manifest(self, source_id: str) -> CaptureCapabilityManifest:
        """Return the registered adapter's bounded capability declaration."""

        source = self.get_source(source_id)
        adapter = self.adapters.get(source.provider)
        if adapter is None:
            return CaptureCapabilityManifest.unavailable(
                source.provider,
                reason="adapter_not_registered",
                connection="unknown",
            )
        return self._adapter_manifest(adapter, source)

    def get_capability_manifest(self, source_id: str) -> CaptureCapabilityManifest:
        """Compatibility alias for callers that use getter-style naming."""

        return self.capability_manifest(source_id)

    def capability_conformance(self, source_id: str) -> CaptureCapabilityConformance:
        return self.capability_manifest(source_id).conformance()

    def enable(self, source_id: str) -> CaptureSource:
        return self.ledger.transition(source_id, "enabled")

    def pause(self, source_id: str) -> CaptureSource:
        return self.ledger.transition(source_id, "paused")

    def resume(self, source_id: str) -> CaptureSource:
        source = self.get_source(source_id)
        if not source.local_only or not source.local_only_acknowledged:
            raise CaptureError("capture_local_only_required")
        target: CaptureLifecycleState = (
            "reconciling" if source.lifecycle_state == "degraded" else "enabled"
        )
        return self.ledger.transition(source_id, target)

    def disable(self, source_id: str) -> CaptureSource:
        return self.ledger.transition(source_id, "disabled")

    def revoke(self, source_id: str) -> CaptureSource:
        return self.ledger.transition(source_id, "revoked")

    def recover_available_adapters(self, *, provider: str | None = None) -> tuple[str, ...]:
        """Clear adapter-unavailable degradation after validated recovery.

        Adapter registration is an ephemeral runtime concern. The source
        lifecycle remains Core-owned, so recovery is performed through the
        ledger and only for sources whose last durable failure was the bounded
        ``capture_adapter_unavailable`` condition.
        """

        sources, _total = self.list_sources(limit=500, offset=0)
        recovered: list[str] = []
        for source in sources:
            if (
                source.lifecycle_state != "degraded"
                or source.last_error_code != "capture_adapter_unavailable"
                or (provider is not None and source.provider != provider)
            ):
                continue
            adapter = self.adapters.get(source.provider)
            if adapter is None:
                continue
            try:
                manifest = self._adapter_manifest(adapter, source)
            except CaptureError:
                continue
            if (
                capture_executable_capability_error(manifest) is not None
                or manifest.authorization != "authorized"
                or manifest.connection != "connected"
            ):
                continue
            self.ledger.recover_adapter_unavailable(source.id)
            recovered.append(source.id)
        return tuple(recovered)

    def _adapter_page(
        self,
        adapter: CaptureProviderAdapter,
        source: CaptureSource,
        cursor: str | None,
        page_order: int,
        manifest: CaptureCapabilityManifest | None = None,
    ) -> CapturePage:
        try:
            page = adapter.fetch_page(source, cursor, page_order)
        except CaptureRetryableError:
            if manifest is not None and not manifest.retry_policy.retryable_failures:
                raise CaptureError("capture_capability_invalid") from None
            raise
        except CaptureError:
            raise
        except Exception as error:
            del error
            raise CaptureError("capture_page_malformed") from None
        if not isinstance(page, CapturePage):
            raise CaptureError("capture_page_malformed")
        return page

    def _adapter_manifest(
        self,
        adapter: CaptureProviderAdapter,
        source: CaptureSource,
    ) -> CaptureCapabilityManifest:
        try:
            raw_manifest: Any = getattr(adapter, "capability_manifest", None)
            if raw_manifest is None:
                return CaptureCapabilityManifest.compatibility_default(source.provider)
            raw_manifest = raw_manifest() if callable(raw_manifest) else raw_manifest
        except CaptureError:
            raise
        except Exception as error:
            del error
            raise CaptureError("capture_capability_invalid") from None
        if not isinstance(raw_manifest, CaptureCapabilityManifest):
            raise CaptureError("capture_capability_invalid")
        try:
            conforms = raw_manifest.conformance().valid
            provider_matches = raw_manifest.provider == source.provider
        except Exception as error:
            del error
            raise CaptureError("capture_capability_invalid") from None
        if not provider_matches or not conforms:
            raise CaptureError("capture_capability_invalid")
        return raw_manifest

    @staticmethod
    def _validate_page_capabilities(
        page: CapturePage,
        manifest: CaptureCapabilityManifest,
    ) -> None:
        if manifest.legacy_compatibility:
            return
        if page.next_cursor is not None and not manifest.cursor_support:
            raise CaptureError("capture_capability_invalid")
        if page.coverage is not None and page.coverage != manifest.coverage:
            raise CaptureError("capture_capability_invalid")
        if page.coverage == "unavailable" and page.events:
            raise CaptureError("capture_capability_invalid")
        if page.freshness is not None and page.freshness != manifest.freshness:
            raise CaptureError("capture_capability_invalid")

    def _retry_backoff(
        self,
        manifest: CaptureCapabilityManifest,
        error: CaptureError | None = None,
    ) -> BackoffPolicy:
        """Use declared bounded provider retry semantics through the existing ledger."""

        if manifest.legacy_compatibility:
            return self.backoff
        if (
            isinstance(error, CaptureRetryableError)
            and error.retry_after_seconds is not None
            and manifest.retry_policy.rate_limit.mode == "retry_after"
        ):
            delay = min(
                error.retry_after_seconds,
                manifest.retry_policy.rate_limit.max_delay_seconds,
            )
            return BackoffPolicy(base_seconds=delay, max_seconds=delay)
        return manifest.retry_policy.backoff

    def _apply(
        self,
        *,
        source_id: str,
        event: CaptureEvent,
        event_id: str,
        run_handle: CaptureRunHandle,
    ) -> tuple[str, str]:
        if self.sink is None:
            raise CaptureError("capture_sink_failed")
        canonical_record_id = _canonical_lineage(source_id, event.provider_item_id)
        try:
            result = self.sink.apply(
                event,
                source_id=source_id,
                event_id=event_id,
                run_handle=run_handle,
                canonical_record_id=canonical_record_id,
                idempotency_key=_idempotency_key(source_id, event.provider_event_id),
            )
        except CaptureError:
            raise
        except Exception as error:
            del error
            raise CaptureError("capture_sink_failed") from None
        if isinstance(result, CaptureApplicationReceipt):
            receipt = result.receipt
            returned_lineage = result.canonical_record_id
        elif isinstance(result, str):
            receipt = result
            returned_lineage = canonical_record_id
        else:
            raise CaptureError("capture_sink_receipt_invalid")
        if (
            not isinstance(receipt, str)
            or not isinstance(returned_lineage, str)
            or returned_lineage != canonical_record_id
        ):
            raise CaptureError("capture_sink_receipt_invalid")
        return receipt, returned_lineage

    @staticmethod
    def _event_from_row(row: Any) -> CaptureEvent:
        try:
            payload = json.loads(str(row["normalized_payload_json"]))
        except (TypeError, ValueError):
            raise CaptureError("capture_failed") from None
        if not isinstance(payload, Mapping):
            raise CaptureError("capture_failed")
        try:
            return CaptureEvent(
                provider_event_id=str(row["provider_event_id"]),
                provider_item_id=str(row["provider_item_id"]),
                order_key=str(row["order_key"]),
                operation=cast(Literal["upsert", "delete"], str(row["operation"])),
                payload=payload,
                generation=int(row["generation"]),
            )
        except (CaptureError, TypeError, ValueError):
            raise CaptureError("capture_failed") from None

    def _recover_pending_page(
        self,
        handle: CaptureRunHandle,
    ) -> tuple[int, int, int, str | None] | None:
        checkpoint = self.ledger._checkpoint(handle.source_id)
        pending = self.ledger._pending_from_checkpoint(checkpoint)
        if pending is None:
            return None
        pending_generation, pending_cursor, event_ids = pending
        with self.ledger.store.connect() as connection:
            rows: dict[str, Any] = {}
            for event_id in event_ids:
                row = connection.execute(
                    "SELECT id,source_id,provider_event_id,provider_item_id,generation,order_key,"
                    "operation,normalized_payload_json,payload_hash,status FROM capture_events "
                    "WHERE id=? AND source_id=?",
                    (event_id, handle.source_id),
                ).fetchone()
                if row is None:
                    raise CaptureError("capture_failed")
                rows[event_id] = row

        applied = 0
        duplicates = 0
        for event_id in event_ids:
            row = rows[event_id]
            if int(row["generation"]) != pending_generation:
                raise CaptureError("capture_event_generation_mismatch")
            event = self._event_from_row(row)
            already_applied = self.ledger._retry_pending_event(handle, event_id)
            try:
                receipt, lineage = self._apply(
                    source_id=handle.source_id,
                    event=event,
                    event_id=event_id,
                    run_handle=handle,
                )
                self.ledger.commit_event(
                    handle=handle,
                    event=event,
                    event_id=event_id,
                    receipt=receipt,
                    canonical_record_id=lineage,
                )
            except CaptureError as error:
                try:
                    self.ledger.mark_event_failure(handle, event_id, error.code)
                except CaptureError as ownership_error:
                    if ownership_error.code != "capture_lease_expired":
                        raise
                    raise ownership_error from error
                raise
            if already_applied:
                duplicates += 1
            else:
                applied += 1

        self.ledger.commit_page_cursor(
            handle,
            CapturePage(
                generation=pending_generation,
                next_cursor=pending_cursor,
                done=True,
                events=(),
            ),
        )
        return len(event_ids), applied, duplicates, pending_cursor

    def run(self, source_id: str) -> CaptureRunResult:
        with self.activity_gate.activity():
            return self._run(source_id)

    def _run(self, source_id: str) -> CaptureRunResult:
        source = self.get_source(source_id)
        if source.lifecycle_state in {"disabled", "paused", "revoked"}:
            return self.ledger.skipped_result(source_id, "capture_source_not_enabled")
        if source.lifecycle_state == "degraded":
            return self.ledger.skipped_result(source_id, "capture_source_degraded")
        adapter = self.adapters.get(source.provider)
        if adapter is None:
            self._mark_unavailable(source_id)
            return self.ledger.skipped_result(source_id, "capture_adapter_unavailable")
        try:
            manifest = self._adapter_manifest(adapter, source)
        except CaptureError as error:
            return self.ledger.skipped_result(source_id, error.code)
        capability_error = capture_executable_capability_error(manifest)
        if capability_error is not None:
            if capability_error == "capture_adapter_unavailable":
                self._mark_unavailable(source_id)
            return self.ledger.skipped_result(source_id, capability_error)
        if (
            not manifest.legacy_compatibility
            and source.retry_count >= manifest.retry_policy.max_attempts
        ):
            return self.ledger.skipped_result(source_id, "capture_retry_exhausted")
        if not self._run_lock.acquire(blocking=False):
            return self.ledger.skipped_result(source_id, "capture_run_in_progress")
        try:
            try:
                handle, active_source, attempt = self.ledger.begin_run(source_id)
            except CaptureError as error:
                return self.ledger.skipped_result(source_id, error.code)
            pages = events = applied = duplicates = failures = 0
            cursor = self.ledger._checkpoint(source_id)["cursor"]
            expected_page_order: int | None = None
            last_error: str | None = None
            try:
                self.ledger.renew_run(handle)
                recovered = self._recover_pending_page(handle)
                if recovered is not None:
                    recovered_events, recovered_applied, recovered_duplicates, cursor = recovered
                    pages += 1
                    events += recovered_events
                    applied += recovered_applied
                    duplicates += recovered_duplicates
                    if cursor is None:
                        return self.ledger.finish_run(
                            handle=handle,
                            status="completed",
                            error_code=None,
                            pages=pages,
                            events=events,
                            applied_events=applied,
                            duplicate_events=duplicates,
                            failures=failures,
                            attempts=attempt,
                            backoff=self._retry_backoff(manifest),
                        )
                remaining_pages = MAX_RUN_PAGES - pages
                if remaining_pages <= 0:
                    raise CaptureError("capture_page_limit_exceeded")
                for page_index in range(remaining_pages):
                    self.ledger.renew_run(handle)
                    page = self._adapter_page(
                        adapter,
                        active_source,
                        cursor,
                        page_index,
                        manifest,
                    )
                    if expected_page_order is None:
                        expected_page_order = page.page_order
                    elif page.page_order != expected_page_order:
                        raise CaptureError("capture_page_malformed")
                    if page.generation < self.ledger._checkpoint(source_id)["generation"]:
                        raise CaptureError("capture_event_generation_mismatch")
                    pages += 1
                    if events + len(page.events) > MAX_RUN_EVENTS:
                        raise CaptureError("capture_event_limit_exceeded")
                    self._validate_page_capabilities(page, manifest)
                    self._validate_page_events(source_id, page)
                    staged = self.ledger.stage_page(handle, page)
                    for event, (event_id, already_applied, _attempts) in zip(
                        page.events, staged, strict=True
                    ):
                        events += 1
                        if already_applied:
                            duplicates += 1
                            continue
                        try:
                            receipt, lineage = self._apply(
                                source_id=source_id,
                                event=event,
                                event_id=event_id,
                                run_handle=handle,
                            )
                            self.ledger.commit_event(
                                handle=handle,
                                event=event,
                                event_id=event_id,
                                receipt=receipt,
                                canonical_record_id=lineage,
                            )
                        except CaptureError as error:
                            try:
                                self.ledger.mark_event_failure(handle, event_id, error.code)
                            except CaptureError as ownership_error:
                                if ownership_error.code != "capture_lease_expired":
                                    raise
                                raise ownership_error from error
                            raise
                        applied += 1
                    self.ledger.commit_page_cursor(handle, page)
                    cursor = page.next_cursor
                    expected_page_order += 1
                    if page.done:
                        break
                else:
                    raise CaptureError("capture_page_limit_exceeded")
                return self.ledger.finish_run(
                    handle=handle,
                    status="completed",
                    error_code=None,
                    pages=pages,
                    events=events,
                    applied_events=applied,
                    duplicate_events=duplicates,
                    failures=failures,
                    attempts=attempt,
                    backoff=self._retry_backoff(manifest),
                )
            except CaptureError as error:
                last_error = error.code
                failures += 1
                try:
                    return self.ledger.finish_run(
                        handle=handle,
                        status="failed",
                        error_code=last_error,
                        pages=pages,
                        events=events,
                        applied_events=applied,
                        duplicate_events=duplicates,
                        failures=failures,
                        attempts=attempt,
                        backoff=self._retry_backoff(manifest, error),
                    )
                except CaptureError as ownership_error:
                    if ownership_error.code != "capture_lease_expired":
                        raise
                    return self.ledger.stale_result(
                        handle,
                        pages=pages,
                        events=events,
                        applied_events=applied,
                        duplicate_events=duplicates,
                        failures=failures,
                    )
            except Exception:
                # Keep an unexpected local storage/runtime failure content-free
                # while closing the durable run instead of leaving it running.
                failures += 1
                try:
                    return self.ledger.finish_run(
                        handle=handle,
                        status="failed",
                        error_code="capture_failed",
                        pages=pages,
                        events=events,
                        applied_events=applied,
                        duplicate_events=duplicates,
                        failures=failures,
                        attempts=attempt,
                        backoff=self.backoff,
                    )
                except CaptureError as ownership_error:
                    if ownership_error.code != "capture_lease_expired":
                        raise
                    return self.ledger.stale_result(
                        handle,
                        pages=pages,
                        events=events,
                        applied_events=applied,
                        duplicate_events=duplicates,
                        failures=failures,
                    )
        finally:
            self._run_lock.release()

    def _validate_page_events(self, source_id: str, page: CapturePage) -> None:
        """Validate a complete page before any event in it can advance state."""

        provider_event_ids: set[str] = set()
        checkpoint = self.ledger._checkpoint(source_id)
        current_generation = int(checkpoint["generation"])
        baseline = (
            cast(str | None, checkpoint["last_order_key"])
            if page.generation == current_generation
            else None
        )
        for event in page.events:
            if event.provider_event_id in provider_event_ids:
                raise CaptureError("capture_event_payload_conflict")
            provider_event_ids.add(event.provider_event_id)
            if event.generation != page.generation:
                raise CaptureError("capture_event_generation_mismatch")
            _payload_json(event.payload)
            existing = self._existing_event(source_id, event.provider_event_id)
            if existing is not None:
                if (
                    existing["payload_hash"] != event.normalized()[1]
                    or existing["provider_item_id"] != event.provider_item_id
                    or existing["operation"] != event.operation
                    or existing["generation"] != event.generation
                    or existing["order_key"] != event.order_key
                ):
                    raise CaptureError("capture_event_payload_conflict")
                if existing["status"] == "applied":
                    continue
            if baseline is not None and not _order_before(baseline, event.order_key):
                raise CaptureError("capture_event_out_of_order")
            if baseline is not None and _order_gap(baseline, event.order_key):
                raise CaptureError("capture_event_gap")
            baseline = event.order_key

    def _existing_event(self, source_id: str, provider_event_id: str) -> dict[str, Any] | None:
        with self.ledger.store.connect() as connection:
            row = connection.execute(
                "SELECT status,payload_hash,provider_item_id,operation,generation,order_key "
                "FROM capture_events WHERE source_id=? AND provider_event_id=?",
                (source_id, provider_event_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "status": str(row["status"]),
            "payload_hash": str(row["payload_hash"]),
            "provider_item_id": str(row["provider_item_id"]),
            "operation": str(row["operation"]),
            "generation": int(row["generation"]),
            "order_key": str(row["order_key"]),
        }

    def _mark_unavailable(self, source_id: str) -> None:
        with self.ledger.store.transaction() as connection:
            now = self.clock()
            row = connection.execute(
                "SELECT retry_count,lifecycle_state FROM capture_sources WHERE id=?", (source_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("capture source not found")
            if str(row["lifecycle_state"]) in {"disabled", "paused", "revoked"}:
                return
            if str(row["lifecycle_state"]) == "reconciling":
                active_run = connection.execute(
                    "SELECT 1 FROM capture_runs "
                    "WHERE source_id=? AND state='running' AND lease_expires_at>? LIMIT 1",
                    (source_id, now),
                ).fetchone()
                if active_run is not None:
                    # Adapter availability is not authority to revoke a live
                    # run owned by another coordinator/process.  The lease
                    # owner must be able to renew and finish while this
                    # content-free probe reports the adapter as unavailable.
                    return
            retry_count = int(row["retry_count"]) + 1
            next_retry = (
                (_parse_time(now) + timedelta(seconds=self.backoff.delay_seconds(retry_count)))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
            connection.execute(
                "UPDATE capture_sources SET lifecycle_state='degraded',retry_count=?,next_retry_at=?,"
                "last_error_code=?,last_error_at=?,updated_at=? WHERE id=?",
                (retry_count, next_retry, "capture_adapter_unavailable", now, now, source_id),
            )


class DeterministicFakeAdapter:
    """Test-only page adapter with no network or credential behavior."""

    def __init__(
        self,
        pages: Iterable[CapturePage],
        *,
        capability_manifest: CaptureCapabilityManifest | None = None,
    ) -> None:
        self.pages = tuple(pages)
        self.calls: list[tuple[str | None, int]] = []
        self.capability_manifest = capability_manifest or CaptureCapabilityManifest(provider="fake")

    def fetch_page(
        self,
        source: CaptureSource,
        cursor: str | None,
        page_order: int,
    ) -> CapturePage:
        del source
        self.calls.append((cursor, page_order))
        if not self.pages:
            return CapturePage(generation=0, events=(), done=True)
        index = min(len(self.calls) - 1, len(self.pages) - 1)
        return self.pages[index]


class IdempotentFakeSink:
    """Test-only sink proving deterministic idempotent replay semantics."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, CaptureOperation]] = []
        self.receipts: dict[str, str] = {}
        self.fail_once_after_apply = False

    def apply(
        self,
        event: CaptureEvent,
        *,
        source_id: str,
        event_id: str,
        run_handle: CaptureRunHandle,
        canonical_record_id: str,
        idempotency_key: str,
    ) -> str:
        del event_id, run_handle
        if idempotency_key in self.receipts:
            return self.receipts[idempotency_key]
        receipt = f"receipt-{len(self.receipts) + 1}"
        self.receipts[idempotency_key] = receipt
        self.calls.append((source_id, canonical_record_id, idempotency_key, event.operation))
        if self.fail_once_after_apply:
            self.fail_once_after_apply = False
            raise CaptureError("capture_sink_failed")
        return receipt


DeterministicFakeSink = IdempotentFakeSink


__all__ = [
    "BackoffPolicy",
    "CaptureAcquisitionMode",
    "CaptureApplicationReceipt",
    "CaptureApplicationSink",
    "CaptureAuthorizationState",
    "CaptureCapabilityAvailability",
    "CaptureCapabilityConformance",
    "CaptureCapabilityManifest",
    "CaptureConnectionState",
    "CaptureCoordinator",
    "CaptureCoverageState",
    "CaptureDeletionCoordination",
    "CaptureError",
    "CaptureEvent",
    "CaptureFreshnessState",
    "CaptureHealthState",
    "CaptureLedger",
    "CaptureLifecycleState",
    "CaptureNetworkAccessState",
    "CapturePage",
    "CaptureProviderAdapter",
    "CapturePurgeCoordination",
    "CaptureRateLimitMode",
    "CaptureRateLimitPolicy",
    "CaptureRetryPolicy",
    "CaptureRetryableError",
    "CaptureRunHandle",
    "CaptureRunResult",
    "CaptureSource",
    "CaptureTransitionError",
    "DeterministicFakeAdapter",
    "DeterministicFakeSink",
    "IdempotentFakeSink",
    "capture_executable_capability_error",
    "ensure_capture_schema",
]
