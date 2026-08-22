# ruff: noqa: E501
"""Content-free Continuous Capture contracts and the local capture ledger.

This module deliberately stops at a provider-neutral, foreground-only ledger.
Adapters are injected by tests or a future connector package; this repository
does not contain a network implementation or a credential-bearing adapter.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from .ids import new_id, utc_now
from .storage import (
    ConflictError,
    CoreStore,
    NotFoundError,
    StorageError,
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
MAX_PAGE_EVENTS = 256
MAX_RUN_PAGES = 100
MAX_RUN_EVENTS = 10_000
MAX_ERROR_CHARS = 96
LEASE_SECONDS = 60

CAPTURE_ERROR_CODES = frozenset(
    {
        "capture_adapter_unavailable",
        "capture_source_not_enabled",
        "capture_source_degraded",
        "capture_run_in_progress",
        "capture_lease_expired",
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
        "capture_invalid_transition",
    }
)

_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:@/+-]+$")
_SECRET_MARKER_RE = re.compile(
    r"(?i)(bearer\s+|basic\s+|sk-[a-z0-9]|gh[pousr]_[a-z0-9]|AIza[a-z0-9]|"
    r"secret\s*[:=]|password\s*[:=]|credential\s*[:=]|token\s*[:=])"
)
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


def _bounded_text(value: str, *, maximum: int, code: str = "capture_page_malformed") -> str:
    text = str(value).strip()
    if not text or len(text) > maximum or _SAFE_TEXT_RE.fullmatch(text) is None:
        raise CaptureError(code)
    if _SECRET_MARKER_RE.search(text):
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


def _normalize_payload(value: Any, *, depth: int = 0, key: str | None = None) -> Any:
    if depth > MAX_PAYLOAD_DEPTH:
        raise CaptureError("capture_payload_rejected")
    if key is not None:
        if len(key) > MAX_PAYLOAD_STRING_CHARS or _SENSITIVE_KEY_RE.search(key):
            raise CaptureError("capture_payload_rejected")
        _bounded_text(key, maximum=MAX_PAYLOAD_STRING_CHARS, code="capture_payload_rejected")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CaptureError("capture_payload_rejected")
        return value
    if isinstance(value, str):
        text = _bounded_text(
            value,
            maximum=MAX_PAYLOAD_STRING_CHARS,
            code="capture_payload_rejected",
        )
        return text
    if isinstance(value, Mapping):
        if len(value) > MAX_PAYLOAD_KEYS:
            raise CaptureError("capture_payload_rejected")
        result: dict[str, Any] = {}
        for raw_key, raw_value in sorted(value.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_key, str):
                raise CaptureError("capture_payload_rejected")
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
        if self.operation not in {"upsert", "delete"} or self.generation < 0:
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

    def __post_init__(self) -> None:
        if self.generation < 0 or self.page_order < 0:
            raise CaptureError("capture_page_malformed")
        if len(self.events) > MAX_PAGE_EVENTS:
            raise CaptureError("capture_event_limit_exceeded")
        if self.next_cursor is not None:
            _bounded_cursor(self.next_cursor)
        if not isinstance(self.done, bool):
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
    """Provider-neutral page source; implementations must be injected."""

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
        canonical_record_id: str,
        idempotency_key: str,
    ) -> str | CaptureApplicationReceipt: ...


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    base_seconds: int = 1
    max_seconds: int = 3_600

    def __post_init__(self) -> None:
        if self.base_seconds < 0 or self.max_seconds < self.base_seconds:
            raise ValueError("invalid capture backoff policy")

    def delay_seconds(self, attempt: int) -> int:
        bounded_attempt = max(1, min(attempt, 31))
        exponential_delay = self.base_seconds * (1 << (bounded_attempt - 1))
        return min(self.max_seconds, exponential_delay)


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


_CAPTURE_MIGRATION_PATH = (
    Path(__file__).parent / "migrations" / "core" / "015_continuous_capture.sql"
)


def ensure_capture_schema(connection: Any) -> None:
    """Repair missing migration-015 tables/indexes after an interrupted startup."""

    migration = _CAPTURE_MIGRATION_PATH.read_text(encoding="utf-8")
    for statement in _migration_statements(migration):
        connection.execute(statement)


class CaptureLedger:
    """Typed repository for migration-015 capture state."""

    def __init__(self, store: CoreStore, *, clock: Callable[[], str] = utc_now) -> None:
        self.store = store
        self.clock = clock

    @staticmethod
    def _source_from_row(row: Any) -> CaptureSource:
        raw_scopes = json.loads(str(row["requested_scopes_json"]))
        scopes = tuple(str(item) for item in raw_scopes) if isinstance(raw_scopes, list) else ()
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
        if len(requested_scopes) > MAX_SCOPE_COUNT:
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
                "SELECT generation,last_order_key,last_event_id,cursor,updated_at "
                "FROM capture_checkpoints WHERE source_id=?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("capture checkpoint not found")
        return {
            key: row[key]
            for key in ("generation", "last_order_key", "last_event_id", "cursor", "updated_at")
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

    def begin_run(self, source_id: str) -> tuple[CaptureRunHandle, CaptureSource, int]:
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
        return CaptureRunHandle._mint(run_id, source_id, lease_token), self.get_source(source_id), attempt

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

    def stage_event(self, handle: CaptureRunHandle, event: CaptureEvent) -> tuple[str, bool, int]:
        payload_json, payload_hash = event.normalized()
        idempotency_key = _idempotency_key(handle.source_id, event.provider_event_id)
        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            existing = connection.execute(
                "SELECT id,status,payload_hash,provider_item_id,operation,generation,order_key,attempts "
                "FROM capture_events "
                "WHERE source_id=? AND provider_event_id=?",
                (handle.source_id, event.provider_event_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["payload_hash"]) != payload_hash
                    or str(existing["provider_item_id"]) != event.provider_item_id
                    or str(existing["operation"]) != event.operation
                    or int(existing["generation"]) != event.generation
                    or str(existing["order_key"]) != event.order_key
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
                    handle.source_id,
                    event.provider_event_id,
                    event.provider_item_id,
                    event.generation,
                    event.order_key,
                    event.operation,
                    payload_json,
                    payload_hash,
                    idempotency_key,
                    now,
                ),
            )
            return event_id, False, 1

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
            if (
                str(stored["provider_event_id"]) != event.provider_event_id
                or str(stored["provider_item_id"]) != event.provider_item_id
                or str(stored["operation"]) != event.operation
                or int(stored["generation"]) != event.generation
                or str(stored["order_key"]) != event.order_key
                or str(stored["payload_hash"]) != event.normalized()[1]
            ):
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

    def commit_page_cursor(self, handle: CaptureRunHandle, page: CapturePage) -> None:
        now = self.clock()
        with self.store.transaction() as connection:
            self._require_active_run(connection, handle, now)
            row = connection.execute(
                "SELECT generation,cursor FROM capture_checkpoints WHERE source_id=?",
                (handle.source_id,),
            ).fetchone()
            if row is None:
                raise ConflictError("capture checkpoint disappeared before page commit")
            if page.generation < int(row["generation"]):
                return
            previous_cursor = cast(str | None, row["cursor"])
            if previous_cursor == page.next_cursor and not page.done:
                raise CaptureError("capture_invalid_cursor")
            connection.execute(
                "UPDATE capture_checkpoints SET generation=?,cursor=?,updated_at=? WHERE source_id=?",
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
            lag_pages = 1 if lag_events else 0
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
    ) -> None:
        self.ledger = CaptureLedger(store, clock=clock)
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

    def _adapter_page(
        self,
        adapter: CaptureProviderAdapter,
        source: CaptureSource,
        cursor: str | None,
        page_order: int,
    ) -> CapturePage:
        try:
            page = adapter.fetch_page(source, cursor, page_order)
        except CaptureError:
            raise
        except Exception as error:
            del error
            raise CaptureError("capture_page_malformed") from None
        if not isinstance(page, CapturePage):
            raise CaptureError("capture_page_malformed")
        return page

    def _apply(
        self,
        *,
        source_id: str,
        event: CaptureEvent,
        event_id: str,
    ) -> tuple[str, str]:
        if self.sink is None:
            raise CaptureError("capture_sink_failed")
        canonical_record_id = _canonical_lineage(source_id, event.provider_item_id)
        try:
            result = self.sink.apply(
                event,
                source_id=source_id,
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
        return receipt, returned_lineage

    def run(self, source_id: str) -> CaptureRunResult:
        source = self.get_source(source_id)
        if source.lifecycle_state in {"disabled", "paused", "revoked"}:
            return self.ledger.skipped_result(source_id, "capture_source_not_enabled")
        if source.lifecycle_state == "degraded":
            return self.ledger.skipped_result(source_id, "capture_source_degraded")
        adapter = self.adapters.get(source.provider)
        if adapter is None:
            self._mark_unavailable(source_id)
            return self.ledger.skipped_result(source_id, "capture_adapter_unavailable")
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
                for page_index in range(MAX_RUN_PAGES):
                    self.ledger.renew_run(handle)
                    page = self._adapter_page(adapter, active_source, cursor, page_index)
                    if expected_page_order is None:
                        expected_page_order = page.page_order
                    elif page.page_order != expected_page_order:
                        raise CaptureError("capture_page_malformed")
                    if page.generation < self.ledger._checkpoint(source_id)["generation"]:
                        raise CaptureError("capture_event_generation_mismatch")
                    pages += 1
                    if events + len(page.events) > MAX_RUN_EVENTS:
                        raise CaptureError("capture_event_limit_exceeded")
                    self._validate_page_events(source_id, page)
                    for event in page.events:
                        event_id, already_applied, _attempts = self.ledger.stage_event(handle, event)
                        events += 1
                        if already_applied:
                            duplicates += 1
                            continue
                        try:
                            receipt, lineage = self._apply(
                                source_id=source_id,
                                event=event,
                                event_id=event_id,
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
                    backoff=self.backoff,
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

        checkpoint = self.ledger._checkpoint(source_id)
        current_generation = int(checkpoint["generation"])
        baseline = (
            cast(str | None, checkpoint["last_order_key"])
            if page.generation == current_generation
            else None
        )
        for event in page.events:
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
        now = self.clock()
        with self.ledger.store.transaction() as connection:
            row = connection.execute(
                "SELECT retry_count,lifecycle_state FROM capture_sources WHERE id=?", (source_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("capture source not found")
            if str(row["lifecycle_state"]) in {"disabled", "paused", "revoked"}:
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

    def __init__(self, pages: Iterable[CapturePage]) -> None:
        self.pages = tuple(pages)
        self.calls: list[tuple[str | None, int]] = []

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
        canonical_record_id: str,
        idempotency_key: str,
    ) -> str:
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
    "CaptureApplicationReceipt",
    "CaptureApplicationSink",
    "CaptureCoordinator",
    "CaptureError",
    "CaptureEvent",
    "CaptureLedger",
    "CaptureLifecycleState",
    "CapturePage",
    "CaptureProviderAdapter",
    "CaptureRunHandle",
    "CaptureRunResult",
    "CaptureSource",
    "CaptureTransitionError",
    "DeterministicFakeAdapter",
    "DeterministicFakeSink",
    "IdempotentFakeSink",
    "ensure_capture_schema",
]
